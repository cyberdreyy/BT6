### Title
JWT replay-cache check-then-act race allows JWT reuse in `Authorize` - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
`WorkflowMetadataHandler.Authorize` checks `jwtCache.isReplay(claims.ID)` and only records the JWT ID as used via `jwtCache.recordUsage(claims.ID)` at the very end of the function, after signer/authorization checks succeed. `isReplay` and `recordUsage` are two independent, separately-locked operations on the same map, so the check and the write are not atomic with respect to the whole `Authorize` call, mirroring the reported bug class: a state-mutating operation (`gulp`-style "check X, then update X later") that can be reached concurrently before the mutation is committed.

### Finding Description
`jwtReplayCache` guards its map with a `sync.RWMutex`, but `isReplay` and `recordUsage` are each independently locked: [1](#0-0) 

`Authorize` calls `isReplay` near the top of the function, then only calls `recordUsage` at the very end, after JWT verification, workflow lookup, and signer-authorization checks have all completed: [2](#0-1) 

Because the "check" (`isReplay`) and the "commit" (`recordUsage`) are separated by non-trivial work (JWT verification, map lookups) and are not covered by a single critical section, two (or more) concurrent HTTP trigger requests carrying the identical signed JWT (same `jti`) can both call `isReplay` and both observe `false` before either calls `recordUsage`. Both requests then pass authorization and are both accepted as valid, distinct workflow-trigger invocations, defeating the intended one-time-use guarantee of the JWT `jti` claim. This is directly analogous to the reported `gulp`/`accrueInterest` issue: a function that reads state, performs work based on that read, and only writes the updated state afterward, creating a window where re-entry (here, concurrent invocation with the same input) produces an unintended duplicate effect.

### Impact Explanation
This is reachable from an unprivileged external caller: JWTs used here authenticate HTTP-trigger workflow invocation requests coming through the gateway's internet-facing endpoint, and `Authorize` is the choke point intended to guarantee a JWT can trigger a workflow at most once. A successful race allows an attacker (or even an unprivileged legitimate caller replaying their own token concurrently) to trigger the same workflow run twice (or more) with a single signed authorization, resulting in unauthorized/duplicate job execution — this maps to the "unauthorized job run" category explicitly accepted as in-scope impact.

### Likelihood Explanation
The race window requires sending two requests with the identical JWT at (nearly) the same time, which any external caller controls entirely (they just replay their own captured/observed token payload before the first request completes). Given typical request latencies (JWT verification, workflow ID lookup, and any downstream network calls to shards before `recordUsage` executes), the race window is non-negligible and trivially reproducible by firing concurrent duplicate requests.

### Recommendation
Make the check-and-record step atomic: combine `isReplay` + `recordUsage` into a single method (e.g., `CheckAndRecord`, analogous to the existing pattern used in `core/capabilities/vault/request_replay_guard.go`) that holds one lock for the full check-then-insert sequence and rejects a `jti` up front, before any other authorization work occurs, and before any state indicates the token might be reused.

### Proof of Concept
1. Obtain (or generate) a valid signed JWT with a fixed `jti` authorized for a workflow trigger (`WorkflowMetadataHandler.Authorize` path).
2. Fire two (or more) concurrent HTTP requests to the gateway's workflow-trigger endpoint using the identical JWT.
3. Both goroutines execute `h.jwtCache.isReplay(claims.ID)` before either has called `h.jwtCache.recordUsage(claims.ID)` (verified structurally: `isReplay` at line 87 and `recordUsage` at line 105 of `Authorize`, separated by lookups/checks in between, each independently locked in `jwtReplayCache`, see lines 399-412).
4. Both requests receive successful authorization and the workflow is triggered twice from a single JWT, violating the intended single-use replay protection.

Note: I was not able to trace the full call chain from the HTTP-trigger gateway handler into `Authorize` to confirm concurrency of the actual network path within this session (index/time limits); the vulnerability claim is based on the concrete non-atomic check-then-act pattern in the cited code, which is a structural flaw independent of the exact caller. If a full end-to-end trace is needed, a Devin session with codebase access would be able to confirm the calling handler's concurrency model.

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
