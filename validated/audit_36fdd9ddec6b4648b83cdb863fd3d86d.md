### Title
JWT replay-protection check is not atomic, allowing a single JWT to authorize duplicate workflow executions - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
`WorkflowMetadataHandler.Authorize()` checks whether a JWT `jti` has already been used, then later records it as used — but the check and the record are two separate, non-atomic operations. Two concurrent HTTP trigger requests carrying the identical (unprivileged, client-signed) JWT can both pass the replay check before either records usage, letting an unprivileged caller trigger the same signed workflow-execution request twice with a single token, analogous to how `acceptCounterOffer()` in the Putty report allowed a stale/duplicate action to be executed because the "cancel" and "fill" state check were not atomic.

### Finding Description
The gateway's `Authorize` method for HTTP-trigger workflow execution requests performs:
1. `h.jwtCache.isReplay(claims.ID)` — a read-locked existence check on the shared `jwtReplayCache`.
2. Several more steps unrelated to the cache (workflow ID lookup, signer authorization).
3. `h.jwtCache.recordUsage(claims.ID)` — a write-locked insertion, only reached at the very end of the function. [1](#0-0) 

The `isReplay` and `recordUsage` operations use independent, short-lived locks rather than a single atomic check-and-set: [2](#0-1) 

Because the "check" (`isReplay`) releases its lock immediately and the "set" (`recordUsage`) is only invoked after unrelated work (map lookups, signer validation) completes, there is a window during which two goroutines processing concurrent requests bearing the *same* JWT (same `jti`, same signature, replayed by an unprivileged network client) can both observe `isReplay == false` and both proceed to be authorized. This mirrors the root cause of the referenced Putty finding: an action (`cancel`) that is supposed to invalidate a prior operation is not guaranteed to execute before a second attempt at the same operation succeeds, so both proceed and the effect happens twice.

The regression/unit test that exists only exercises the *sequential* case (`Authorize` called twice in series), not the concurrent race: [3](#0-2) 

The equivalent Vault path in this codebase does the check-and-record atomically under a single lock via `RequestReplayGuard.CheckAndRecord`, showing the safe pattern that `jwtReplayCache` should have followed but does not: [4](#0-3) 

### Impact Explanation
An unprivileged client (anyone who can reach the gateway's HTTP trigger endpoint and possesses one valid signed JWT for a workflow-execute request) can race two copies of the same request to the gateway. If both hit the `Authorize` window before either records the `jti`, the workflow-execution request is forwarded to the DON twice, causing duplicate job execution / duplicate on-chain or off-chain side effects (e.g., duplicate transmissions, duplicate fund movement, or double-spending workflow-triggered actions) from what was authorized as a single-use request. This is a request-impersonation/replay-bypass analogous to unauthorized job run duplication.

### Likelihood Explanation
Exploitation requires only sending the same signed request twice in rapid succession over the network (trivial to script), no privileged access or node compromise needed, and no cryptographic weakness — purely a TOCTOU race in the replay cache. The race window is small but reliably reachable given standard network jitter and the fact that `Authorize` does non-trivial work (signature verification, map lookups) between the check and the record, widening the window enough to be practically triggerable.

### Recommendation
Make the check-and-record operation atomic by holding a single lock for the entire "check does jti exist, if not, then insert" sequence — mirroring `RequestReplayGuard.CheckAndRecord` used elsewhere in this codebase:
```go
func (cache *jwtReplayCache) checkAndRecord(jti string) bool {
    cache.mu.Lock()
    defer cache.mu.Unlock()
    if _, exists := cache.cache[jti]; exists {
        return false
    }
    cache.cache[jti] = time.Now()
    return true
}
```
and call it once at the top of `Authorize`, rejecting immediately on failure, before performing any other authorization work.

### Proof of Concept
1. Attacker obtains one valid signed JWT (`jti = X`) for a legitimate `MethodWorkflowExecute` request (this can be their own legitimately-signed request — the vulnerability is not about forging a signature, but about reusing one within a race window).
2. Attacker fires two (or more) copies of the identical HTTP trigger request containing this JWT to the gateway concurrently.
3. Both goroutines call `Authorize`, and both call `h.jwtCache.isReplay(claims.ID)` before either calls `h.jwtCache.recordUsage(claims.ID)` (widened by intervening signer/workflow lookups) — both return `false` for replay.
4. Both requests pass authorization and are forwarded via `mockDon.SendToNode`/DON dispatch, resulting in the workflow being executed twice from a single authorized JWT, in violation of the intended single-use replay protection demonstrated by the existing sequential test at [3](#0-2) .

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

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```
