### Title
Non-atomic check-then-act in JWT replay cache allows same `jti` to pass `Authorize` concurrently before `recordUsage` — ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
Note: the code referenced by the question (`jwtCache.isReplay`, `WorkflowMetadataHandler`) does not exist in `core/web/presenters/vault.go` (that file only contains DKG-result presenters); it actually lives in `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`. Evaluating the real code: `Authorize` calls `isReplay` and `recordUsage` as two separate, non-atomic locked operations, creating a TOCTOU window where two concurrent calls with the same `claims.ID` (jti) can both pass the replay check before either records usage.

### Finding Description
`jwtReplayCache.isReplay` takes an `RLock`, checks map membership, and releases the lock [1](#0-0) . `WorkflowMetadataHandler.Authorize` then performs additional work (authorized-key lookups) before calling `recordUsage`, which acquires a separate `Lock` to insert the jti [2](#0-1) . Because the check (`isReplay`) and the write (`recordUsage`) are not performed under a single critical section, two goroutines invoking `Authorize` concurrently with an identical `claims.ID` can both observe "not replayed" before either inserts the entry, both passing the one-time-use guard.

However, downstream in `httpTriggerHandler.HandleUserTriggerRequest`, execution dispatch to the DON is gated by `setupCallback`, which performs an atomic check-and-insert on `h.callbacks[requestID]` under `callbacksMu`, rejecting a second request with the same `requestID` with `ErrConflict` [3](#0-2) . The JWT's digest claim is bound to `req.Digest()` of the full JSON-RPC request (method/params/ID) during verification [4](#0-3) , so a genuinely identical replayed request (same digest) would carry the same `req.ID` and be blocked by this atomic, properly-guarded dedup, independent of the JWT cache race. I was not able to confirm from this repo's code exactly which fields `jsonrpc.Request.Digest()` covers (it is defined in the external `chainlink-common` module and not indexed here), so I cannot fully rule out an edge case where digest binding does not cover `ID`, which would be needed for the race to translate into an actual duplicate DON dispatch.

### Impact Explanation
The concrete, verifiable impact is a violation of the documented one-time-use invariant of the JWT replay cache itself (`jwtReplayCache` — "manages used JWT IDs to prevent replay attacks") [5](#0-4) : under concurrency, the same jti can be accepted by `Authorize` more than once. Whether this extends to actually triggering a duplicate DON workflow execution (fund/credential "double-spend") is not established with certainty, because `setupCallback`'s atomic `requestID`-keyed dedup independently blocks duplicate dispatch for identical requests bound to the same signed digest.

### Likelihood Explanation
Exploiting the `isReplay`/`recordUsage` race requires an attacker who already possesses one valid, unexpired, correctly-signed JWT (an unprivileged holder of a workflow trigger credential satisfies the "unprivileged attacker" bar) and the ability to fire two requests concurrently to the gateway with identical `claims.ID`. The window between `isReplay` and `recordUsage` is small but real and race-detectable (`go test -race` or high-concurrency table test would expose it deterministically under load, since it's an unguarded check-then-act, not a narrow single-instruction race).

### Recommendation
Make replay-check-and-record atomic: hold a single `Lock` (not `RLock`) across "check exists, then insert" in one critical section (e.g., a `CheckAndSet` method on `jwtReplayCache` that returns `false` if already present and inserts a Now() timestamp otherwise), and call it once in `Authorize` before doing key-authorization checks, rather than as two separately-locked calls straddling other logic.

### Proof of Concept
Go concurrency test in `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go`:
1. Construct a `WorkflowMetadataHandler` with a registered `workflowID` and authorized key for a test signer.
2. Create two valid JWTs (or one JWT reused for two request objects) sharing the same `claims.ID`, each with request digests matching their respective `req` payloads (requires digest to match each request individually, or use the identical request object in both goroutines to isolate the cache race from digest binding).
3. Launch two goroutines calling `h.Authorize(workflowID, token, req)` simultaneously with a `sync.WaitGroup`, injecting a small artificial delay between `isReplay` and `recordUsage` (e.g., via a test hook or by wrapping `authorizedKeys` lookups with `runtime.Gosched()`/manual sleep) to widen the race window deterministically.
4. Assert that in at least one run (or under `-race -count=100`), both goroutines return `nil` error (both succeed), rather than exactly one succeeding and one returning "JWT token has already been used".
5. Additionally add a separate test proving `setupCallback` correctly rejects same-`requestID` concurrent inserts, to document that the full duplicate-dispatch impact is bounded by that independent atomic guard.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L29-34)
```go
// jwtReplayCache manages used JWT IDs to prevent replay attacks
type jwtReplayCache struct {
	mu            sync.RWMutex
	cleanupPeriod time.Duration
	cache         map[string]time.Time // jti -> timestamp
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-108)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-405)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```

**File:** core/utils/jwt.go (L277-301)
```go
	reqDigest, err := req.Digest()
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	if verifiedClaims.ID == "" {
		return nil, gethcommon.Address{}, errors.New("JWT ID (jti) is required but missing")
	}
	if verifiedClaims.ExpiresAt == nil {
		return nil, gethcommon.Address{}, errors.New("expiredAt (exp) is required but missing")
	}
	if verifiedClaims.IssuedAt == nil {
		return nil, gethcommon.Address{}, errors.New("issuedAt (iat) is required but missing")
	}
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
	if verifiedClaims.Digest != "0x"+reqDigest {
		return nil, gethcommon.Address{}, fmt.Errorf("claim digest '%s' does not match calculated request digest '0x%s'", verifiedClaims.Digest, reqDigest)
	}
```
