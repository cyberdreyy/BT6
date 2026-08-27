### Title
Non-atomic check-then-record JWT replay guard in `WorkflowMetadataHandler.Authorize` allows replay-guard bypass under concurrent requests - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
`WorkflowMetadataHandler.Authorize` is the gateway-side entry point that authenticates unprivileged HTTP-trigger workflow requests using a signed JWT. It performs replay protection by checking a shared `jwtReplayCache` before doing the authorization work, and only marks the JWT `jti` as used at the very end of the function. Because the "check" and the "record" are two separate, independently-locked operations with unrelated work (map lookups, signer verification) executed in between, an attacker who fires several requests concurrently with the same JWT can pass the replay check multiple times before any of them records the `jti` as used — the same TOCTOU (time-of-check/time-of-use) class of bug described in the PRBProxy report, where a guard value is read, other logic executes, and the guard state can change in that window without being re-validated.

### Finding Description
`Authorize` reads the replay cache, then does unrelated work, then writes to the replay cache: [1](#0-0) 

Step by step:
1. `h.jwtCache.isReplay(claims.ID)` takes an `RLock`, checks map membership, and releases the lock. [2](#0-1) 
2. Between that check and the eventual `recordUsage` call, the function looks up `h.authorizedKeys[workflowID]` and verifies the ECDSA signer is authorized — none of this is synchronized with the replay cache.
3. Only after all of this succeeds does `h.jwtCache.recordUsage(claims.ID)` take a separate `Lock` and write the `jti` into the cache. [3](#0-2) 

Because `isReplay` (read) and `recordUsage` (write) are not part of a single atomic critical section, two or more goroutines processing the same JWT concurrently (e.g. duplicate/parallel HTTP requests to the gateway with the same `Authorization` header) will all pass the `isReplay` check before any of them completes `recordUsage`. This is structurally identical to the PRBProxy `_safeDelegateCall` bug: a security-critical value (`owner` there, "already used" state here) is captured/checked once, an intervening operation is allowed to run, and only a final, disconnected step commits the updated state — so a race window exists where the guarantee ("owner never changes" / "JWT used only once") is not actually enforced during the intervening window.

### Impact Explanation
The JWT replay cache is the sole mechanism preventing a given signed JWT (bound to a specific `jsonrpc.Request` payload via `VerifyRequestJWT`) from being used more than once. If an unprivileged external caller can win this race, the single-use guarantee for the JWT is broken, permitting the identical authorized request to be accepted multiple times concurrently. Since the JWT is meant to authorize a specific one-time gateway/HTTP-trigger workflow invocation, bypassing the one-time-use property enables duplicate/concurrent workflow triggering with a token intended for a single use, and undermines a security control that operators may rely on for idempotency/anti-replay guarantees on internet-facing gateway endpoints.

### Likelihood Explanation
Exploitation only requires an unprivileged external caller who already possesses one valid signed JWT (which they legitimately obtained to trigger their own workflow) to send it via multiple concurrent HTTP requests to the gateway. No special privileges beyond normal API access are needed, and the race window (map lookup + signer comparison) is non-trivial in duration relative to typical request handling, making concurrent duplicate submissions a realistic and low-effort attack vector.

### Recommendation
Make the check-and-record operation atomic: acquire a single lock (or use a `sync.Map`/atomic compare-and-swap keyed by `jti`) that performs "insert-if-absent" as one operation, returning failure if the `jti` is already present, rather than separate `isReplay()` read and `recordUsage()` write calls with authorization logic executed in between. This mirrors the PRBProxy recommendation of not relying on a value re-check after intervening work — instead, the guard state must be updated at the same time it is checked so no window exists for concurrent bypass.

### Proof of Concept
1. Obtain a validly signed JWT for a `WorkflowExecute` request (as in `TestWorkflowMetadataHandler_Authorize`'s "JWT replay protection" subtest). [4](#0-3) 
2. Instead of sending it once and then again sequentially (as the existing test does, which correctly rejects the second sequential call), send N copies of the request concurrently (e.g. via goroutines or parallel HTTP clients) using the identical `tokenString`.
3. Because `isReplay` and `recordUsage` are not executed under one lock, multiple goroutines can observe `isReplay(claims.ID) == false` before any of them calls `recordUsage(claims.ID)`, so more than one concurrent call to `Authorize` succeeds for the same `jti`, defeating the intended single-use replay protection.

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
