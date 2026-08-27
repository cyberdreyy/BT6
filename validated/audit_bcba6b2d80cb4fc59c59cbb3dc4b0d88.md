Confirmed: the code exactly matches the described vulnerability.

### Title
JWT jti replay check TOCTOU race allows double-use of a single authorized JWT - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `h.jwtCache.isReplay(claims.ID)` and later calls `h.jwtCache.recordUsage(claims.ID)` as two separate, non-atomic critical sections protected by independent lock acquisitions on the same `sync.RWMutex`. An attacker who fires two (or more) concurrent gateway requests using the identical signed JWT can have both requests pass the `isReplay` check before either reaches `recordUsage`, allowing the same signed workflow-trigger authorization to be consumed more than once.

### Finding Description
`Authorize` is the gateway-side entry point that authenticates a workflow trigger request bound to a JWT (`utils.VerifyRequestJWT`), then enforces single-use of the token via `jwtCache`: [1](#0-0) 

The replay cache implementation exposes `isReplay` (read lock, checks map membership) and `recordUsage` (write lock, inserts jti) as two independent operations, each acquiring and releasing `cache.mu` separately rather than a single atomic check-and-set: [2](#0-1) 

Because `isReplay` and `recordUsage` are not combined into one critical section, if two goroutines call `Authorize` concurrently with the same `claims.ID` (jti), both can execute `isReplay` and observe "not present" before either executes `recordUsage`. Both then proceed through the authorized-key checks (lines 92–104) and both return successfully with the same `*gateway.AuthorizedKey`, meaning the workflow trigger is authorized/executed twice from a single valid signature — defeating the intended one-time-use guarantee of the JWT jti and violating request-binding/replay-protection soundness. An attacker only needs to hold one valid signed JWT (as an authorized workflow-triggering party) and send it twice concurrently to double-fire the associated trigger/action, potentially causing double execution of a workflow trigger or job run.

### Impact Explanation
This breaks the single-use guarantee of the JWT-based workflow trigger authorization, enabling an authorized-but-restricted caller (holder of one valid JWT) to trigger the corresponding workflow twice using only one authorized signature. This maps to Chainlink's "unauthorized job run" / authentication soundness impact class — the replay-prevention mechanism is the sole gate against re-execution from a single credential, and the race defeats it under concurrent delivery, which is trivially reproducible by an attacker sending the two requests back-to-back.

### Likelihood Explanation
The precondition is simply possession of one valid, signed JWT for a workflow trigger request (the level of access already required to call `Authorize` legitimately once) plus the ability to send two requests concurrently over the network — no elevated privilege beyond that of a normal authorized caller is needed. The race window is realistic: `isReplay`/`recordUsage` do real work (map lookups, RLock/Lock handoff) with no serialization across the two calls, and network-level concurrency (two near-simultaneous HTTP/gateway requests) is trivial for any attacker to produce. This is highly repeatable and requires no timing luck beyond ordinary concurrent request dispatch.

### Recommendation
Merge the check and record steps into a single atomic operation under one lock, e.g. add a `checkAndRecord(jti string) bool` method on `jwtReplayCache` that acquires `cache.mu.Lock()` once, checks for existence, and if absent immediately inserts before releasing the lock, returning whether it was a replay. Update `Authorize` to call this single atomic method instead of separate `isReplay`/`recordUsage` calls.

### Proof of Concept
Go concurrency test in `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go`:
1. Construct a `WorkflowMetadataHandler` with a valid `workflowID` registered in `authorizedKeys` for a known signer.
2. Generate one valid JWT (`token`) signed by that authorized key, with a fixed `jti`.
3. Launch two goroutines simultaneously (synchronized via a `sync.WaitGroup`/start barrier) that both call `h.Authorize(workflowID, token, req)` with the identical token.
4. Collect both results; assert that in a tight-loop repeated run (e.g., 100 iterations) at least one execution produces both calls returning `nil` error (both succeeding), demonstrating that the replay guard does not reliably reject the second, concurrent identical jti — i.e., assert failure of the invariant "exactly one call succeeds" under race, using `go test -race` to also confirm no serialization exists between the two lock sections.

### Citations

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
