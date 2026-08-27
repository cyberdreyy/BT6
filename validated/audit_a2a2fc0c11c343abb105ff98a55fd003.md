### Title
JWT replay protection defeated by TOCTOU race between `isReplay` check and `recordUsage` write - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `h.jwtCache.isReplay(claims.ID)` and only calls `h.jwtCache.recordUsage(claims.ID)` after all authorization checks succeed, with no atomic check-and-set between the two operations. Two concurrent `Authorize` calls carrying the identical JWT (same `jti`) can both observe "not yet replayed" and both proceed to be treated as validly authorized, defeating single-use JWT replay protection.

### Finding Description
In `Authorize` [1](#0-0) , the sequence is: verify JWT → `isReplay(claims.ID)` (read-lock only, returns bool) → lookup authorized keys/signer → `recordUsage(claims.ID)` (write-lock, happens last, only on success path). `isReplay` and `recordUsage` are two independent lock acquisitions on `jwtCache.mu` rather than a single atomic check-and-set operation [2](#0-1) . If two goroutines call `Authorize` concurrently with the same token, both can execute `isReplay` before either executes `recordUsage`, since `isReplay` only takes an `RLock` and releases it immediately without registering the `jti`. Both goroutines will then see `exists == false`, proceed through signer authorization, and both call `recordUsage` (harmlessly idempotent on the map), but critically both callers receive a successful `*gateway.AuthorizedKey` return value — meaning both requests are authorized to proceed as legitimate, single-use-token-backed trigger invocations.

I was unable to fully trace the exact caller path from an HTTP/gateway route into `Authorize` (searches for `HandleUserTriggerRequest`/`ServeHTTP` in `http_trigger_handler.go` and `http_handler.go` returned no matches in the indexed content, likely due to index size limits), so I cannot confirm from the indexed code whether `Authorize` calls for the same workflow/token are otherwise serialized upstream (e.g., by a per-`jti` or per-connection lock) before reaching this handler. Based solely on `workflow_metadata_handler.go`, the replay-cache logic itself provides no atomicity guarantee.

### Impact Explanation
If reachable concurrently from attacker-controlled input (a JWT is attacker-supplied/attacker-held credential from a legitimate single-use grant), this allows request impersonation/double-use of a single-use authorization token — the described "double-execution of a single authorized trigger request" — which matches an authentication/request-binding bypass impact class (unauthorized duplicate action using a token meant to authorize exactly one action).

### Likelihood Explanation
Exploitability requires only that an attacker hold one valid JWT and can send two requests essentially simultaneously to the gateway node hosting this handler — no elevated privileges beyond already holding a valid single-use token are needed. The race window is narrow (a single map read vs. write under `sync.RWMutex`), but is a real, unbounded TOCTOU gap with no mutex serializing the whole check-then-act sequence, and is repeatable on demand by the token holder. Because I could not verify from the retrieved code whether an upstream lock already serializes `Authorize` invocations for the same request/token before reaching this function, the practical likelihood is uncertain and should be confirmed by tracing the caller (e.g., `http_trigger_handler.go`) in the full repository.

### Recommendation
Replace the separate `isReplay` + `recordUsage` calls with a single atomic check-and-set method, e.g. `checkAndRecord(jti string) bool` that takes the write lock once, checks existence, and inserts the entry in one critical section, returning whether it was already present. Call this single method at the point where replay is currently checked in `Authorize`, and reject if it returns "already used."

### Proof of Concept
1. In `workflow_metadata_handler_test.go`, construct a `WorkflowMetadataHandler` with a registered workflow ID and authorized signer key.
2. Generate one valid JWT (single `jti`) signed by an authorized key for that workflow.
3. Launch two goroutines that both call `h.Authorize(workflowID, token, req)` simultaneously (e.g., synchronized with a `sync.WaitGroup` and a start barrier channel to maximize the chance both pass `isReplay` before either calls `recordUsage`).
4. Assert: currently, both goroutines can return `(key, nil)` (no error) — demonstrating replay protection is bypassed under concurrency.
5. After applying the atomic check-and-set fix, assert exactly one goroutine receives `(key, nil)` and the other receives the "JWT token has already been used" error, run repeatedly (e.g., in a loop with `-race`) to confirm no residual race.

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
