### Title
JWT replay-cache TOCTOU race allows double-execution of a single authorized trigger request - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `h.jwtCache.isReplay(claims.ID)` and later calls `h.jwtCache.recordUsage(claims.ID)`, but these are two separate, independently-locked operations with no lock held across the check-then-act sequence. Two concurrent `Authorize` calls carrying the same JWT `jti` can both pass the `isReplay` check before either commits `recordUsage`, letting a single legitimately-issued JWT be accepted twice.

### Finding Description
`isReplay` takes an `RLock`, checks map membership, and releases the lock [1](#0-0) . Only after signer/key validation does `Authorize` call `recordUsage`, which takes a separate `Lock` and writes the entry [2](#0-1) . Between the `isReplay` read and the `recordUsage` write in `Authorize`, no mutex is held to make the check-then-record sequence atomic [3](#0-2) . If two goroutines call `Authorize` concurrently with the identical `claims.ID` (same JWT), both can execute `isReplay` and observe "not present" before either has called `recordUsage`, so both proceed to validate the signer against `authorizedKeys` and both return a valid `*gateway.AuthorizedKey`, resulting in two authorized executions from a single JWT.

This is a genuine logic bug (classic TOCTOU), not a mock/config issue, and it sits directly in the authorization path invoked per-request. However, I was unable to fully trace the call site (`HandleUserTriggerRequest` in `http_trigger_handler.go`) within the available iterations to confirm whether an upstream per-workflow or per-connection serialization lock exists that would prevent two `Authorize` calls for the same JWT from actually running concurrently (e.g., single-threaded processing per gateway connection, or an outer request-level mutex). The grep results indicate `Authorize` is called from `http_trigger_handler.go` and `http_handler.go`, but I could not verify their concurrency model before running out of tool budget. Given the exact reachable path from the gateway HTTP entrypoint to `Authorize` was not fully confirmed (whether concurrent requests can actually reach `Authorize` in parallel for the same token, or whether a single node processes requests sequentially per shard), I cannot fully validate the exploitability preconditions per the rules requiring a "exact reachable path from the attacker's request... into the affected function" to be traced end-to-end.

### Impact Explanation
If exploitable, impact is limited to a duplicated execution of the attacker's own authorized trigger (their own workflow, their own JWT) — i.e., a quota/duplicate-execution bypass, not cross-user data or fund access, since the attacker must already hold a validly-signed JWT scoped to their own workflow, and the signer/authorizedKeys check still applies to both racing calls.

### Likelihood Explanation
Requires the attacker to already possess one valid, unexpired JWT for their own workflow and fire it as two near-simultaneous requests — feasible for an attacker who controls request timing/network trickery to win the race window between `isReplay` and `recordUsage` (which is small — code between the two calls only does a map lookup and a struct build). Repeatability depends on network jitter to hit the race window each time; not guaranteed on every attempt but is a real, non-deterministic race.

### Recommendation
Merge the check-and-record into a single atomic operation under one lock, e.g. add a `checkAndRecord(jti string) bool` method that takes `cache.mu.Lock()` once, checks existence, and inserts if absent, returning whether it was already present — replacing the separate `isReplay`/`recordUsage` calls in `Authorize`.

### Proof of Concept
Go test plan:
1. Construct a `jwtReplayCache` (or full `WorkflowMetadataHandler` with a fixed signer/workflow setup) and a JWT with a fixed `jti`.
2. Launch two goroutines that both call `Authorize(workflowID, token, req)` (or directly `isReplay`+`recordUsage` sequence) simultaneously using a `sync.WaitGroup` and a barrier channel to maximize the race window.
3. Assert that exactly one goroutine's call succeeds (returns non-nil key) and the other returns the "already been used" error.
4. Run with `go test -race` — expect no data race reported (mutexes protect individual map accesses) but expect the count of successes to sometimes be 2 instead of 1, demonstrating the logical TOCTOU failure despite the absence of a data race.

Note: this PoC validates the cache-level race in isolation; validating full end-to-end exploitability through the gateway HTTP handler requires confirming (in `http_trigger_handler.go`/`http_handler.go`, not fully reviewed here) that concurrent requests for the same JWT can actually reach `Authorize` in parallel without additional serialization.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L87-105)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L407-412)
```go
func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```
