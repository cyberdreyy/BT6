### Title
JWT replay cache TOCTOU race allows same single-use JWT to authorize two concurrent executions - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks JWT replay status via `jwtReplayCache.isReplay` and marks it used via `jwtReplayCache.recordUsage` as two separate, non-atomic locking operations. Two concurrent `Authorize()` calls with the same `jti` can both observe `isReplay == false` before either calls `recordUsage`, breaking the single-use invariant of the JWT.

### Finding Description
In `Authorize` [1](#0-0) , the sequence is: verify JWT signature/claims, call `h.jwtCache.isReplay(claims.ID)` (acquires `RLock`, checks map membership, releases lock), then perform authorization checks, and finally call `h.jwtCache.recordUsage(claims.ID)` (acquires `Lock`, writes to map, releases lock). The `isReplay` read and the `recordUsage` write are two independent critical sections rather than one atomic check-and-set operation: [2](#0-1) 

If two goroutines call `Authorize` concurrently with the same token, both can execute `isReplay` before either executes `recordUsage`, since nothing serializes the check against the eventual write. Both calls will see `exists == false`, both pass authorization checks against the authorized-keys map, and both call `recordUsage` (idempotently overwriting the same key) — but by that point both callers have already received a successful `*gateway.AuthorizedKey` return value. There is no gateway-level request de-duplication or per-`jti` mutex serializing calls to `Authorize` before this point, so nothing else in the reachable path stops it.

### Impact Explanation
This breaks the "request binding" invariant (one signed JWT should authorize exactly one execution). An attacker holding a single valid, single-use JWT for an authorized signer can race two simultaneous requests to the gateway and have both accepted, effectively doubling the authorized action (e.g., triggering the workflow run twice from one signed authorization), which maps to an authorization/quota bypass class of impact rather than a purely cosmetic race.

### Likelihood Explanation
Exploitation requires only possession of one valid signed JWT (no privileged access) and the ability to send two requests to the gateway near-simultaneously (trivial for a network client, no special timing precision beyond firing two HTTP/gateway requests back-to-back). This is fully attacker-controlled and repeatable; the race window is bounded only by the time between the `isReplay` read and the `recordUsage` write within `Authorize`, but concurrent goroutines/requests hitting the gateway can reliably trigger the window under load or by intentionally sending duplicate requests concurrently.

### Recommendation
Make the check-and-set atomic: hold a single write lock across both the existence check and the insertion (a "compare-and-swap" style `checkAndRecord(jti) bool` method) so only one caller can win the race for a given `jti`. For example, replace `isReplay`/`recordUsage` with a single method that under one `Lock()`/`Unlock()` checks `cache[jti]` and, if absent, sets it and returns "not a replay", else returns "replay" — and have `Authorize` call this atomic method instead of the two separate calls.

### Proof of Concept
Go test (`workflow_metadata_handler_test.go` or a dedicated `jwtReplayCache` test):
1. Construct a `WorkflowMetadataHandler` (or the `jwtReplayCache` directly) with a workflow ID registered with one authorized signer, and craft one valid signed JWT for that signer with a fixed `jti`.
2. Spawn two goroutines that concurrently call `h.Authorize(workflowID, token, req)` with the same token, using a `sync.WaitGroup` to synchronize start (e.g., both blocked on a channel released simultaneously) to maximize the race window.
3. Use an atomic/mutex-guarded counter to count how many calls return `(key, nil)` (success) vs. an error.
4. Assert that exactly one call succeeds and one call returns the "JWT token has already been used" error; if the test flakily reports both succeeding (or does so reliably when a small sleep is inserted between the `isReplay` check and `recordUsage` in a test-only build), it confirms the TOCTOU race in `jwtReplayCache`.
5. Optionally, add a direct unit test on `jwtReplayCache` calling `isReplay`/`recordUsage` concurrently from N goroutines and asserting the invariant that only one goroutine observes `isReplay() == false` for the same `jti` before recording — this will fail with the current split-lock implementation.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-412)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```
