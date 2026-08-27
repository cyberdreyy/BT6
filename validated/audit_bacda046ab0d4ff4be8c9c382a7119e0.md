This confirms a real TOCTOU race condition in the JWT replay-cache.

### Title
JWT replay cache TOCTOU race allows double-use of a single signed token - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `h.jwtCache.isReplay(claims.ID)` and later calls `h.jwtCache.recordUsage(claims.ID)` as two separate, non-atomic locked operations, with unrelated authorization work (map lookups) executed in between. Two concurrent `Authorize` calls carrying the same JWT (same `jti`) can both pass `isReplay` before either calls `recordUsage`, allowing a single captured/leaked signed JWT to authorize two workflow executions instead of one.

### Finding Description
`Authorize` at [1](#0-0)  performs: verify JWT → `isReplay(claims.ID)` check → authorized-key lookup → `recordUsage(claims.ID)`. `isReplay` and `recordUsage` are implemented as independent critical sections, each acquiring and releasing the cache mutex separately: [2](#0-1) . There is no single lock held across the "check" and "mark used" steps, and no atomic check-and-set (e.g., `LoadOrStore`) primitive is used. Consequently, if two goroutines invoke `Authorize` with the same token concurrently, both can execute `isReplay` and observe `exists == false` before either executes `recordUsage`, since the mutex is released between the two calls and the authorized-key map lookup (lines 92-104) sits in between with no locking relevant to the jti. Both requests then pass the check, both call `recordUsage` (which is idempotent, only overwriting the timestamp), and both proceed with a valid `*gateway.AuthorizedKey`, causing the workflow action to be authorized/triggered twice from a single valid signature. The existing sequential replay test at [3](#0-2)  only proves the check works for serialized calls; it does not exercise the concurrent path and therefore does not catch this race.

### Impact Explanation
An attacker who captures one valid signed JWT (e.g., via network observation, log leakage, or a compromised proxy) can fire two near-simultaneous requests to trigger the same workflow, causing the workflow action (e.g., initiating a run, potentially with fund-moving or state-changing side effects) to be double-triggered from a single authorized signature. This maps to Chainlink's "unauthorized job run" / authentication-soundness bounty class — the replay protection meant to enforce "one execution per signed token" can be bypassed to get two executions.

### Likelihood Explanation
Exploitation requires only capturing one legitimately signed JWT (no key compromise) and the ability to send two requests with tight timing (a small race window, but reliably reproducible with concurrent goroutines/parallel HTTP requests, as gateway request handling for a given workflow is not otherwise serialized per-jti). No elevated privileges are needed beyond what's needed to submit gateway requests at all — an unprivileged attacker in possession of a leaked token is sufficient. This is a straightforward, repeatable race, not a probabilistic timing attack requiring extreme precision.

### Recommendation
Make the check-and-mark atomic: acquire the cache's write lock once and perform both the existence check and the insertion within the same critical section (or use a `sync.Map`/mutex-guarded map with a single `LoadOrStore`-style helper, e.g. `checkAndRecord(jti)` that returns `false` if already present, else inserts and returns `true`), and call this single atomic operation from `Authorize` instead of separate `isReplay`/`recordUsage` calls.

### Proof of Concept
Go test in `workflow_metadata_handler_test.go`:
1. Set up `handler` with one authorized key/workflow as in `TestWorkflowMetadataHandler_Authorize`.
2. Create one valid signed JWT (`tokenString`) via `utils.CreateRequestJWT` + `SignedString`.
3. Launch two goroutines concurrently, both calling `handler.Authorize(workflowID, tokenString, req)`, synchronized to start with a `sync.WaitGroup`/barrier to maximize overlap; collect `(key, err)` results via a channel.
4. Assert that exactly one call returns `err == nil` with a non-nil key and the other returns the "JWT token has already been used" error.
5. Run with `go test -race -count=100` (or loop the two-goroutine race many times) to demonstrate that, without an atomic check-and-set fix, both calls sometimes succeed (violating the intended single-use invariant), confirming the TOCTOU race in `isReplay`/`recordUsage`.

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go (L1193-1217)
```go
	t.Run("JWT replay protection", func(t *testing.T) {
		params := json.RawMessage(`{"test": "data"}`)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-replay",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &params,
		}

		token, err := utils.CreateRequestJWT(*req)
		require.NoError(t, err)

		tokenString, err := token.SignedString(privateKey)
		require.NoError(t, err)

		key, err := handler.Authorize(workflowID, tokenString, req)
		require.NoError(t, err)
		require.NotNil(t, key)

		// Second authorization with same JWT should fail (replay attack)
		key, err = handler.Authorize(workflowID, tokenString, req)
		require.Error(t, err)
		require.Contains(t, err.Error(), "JWT token has already been used. Please generate a new one with new id (jti)")
		require.Nil(t, key)
	})
```
