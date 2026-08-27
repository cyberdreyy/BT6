### Title
JWT replay-guard check-and-record race allows workflow authorization token reuse - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
The Ambire finding is about a security invariant (nonce-based single-use authorization) that is not enforced atomically with the action it is supposed to gate, allowing the same authorization to be consumed more than once. The closest reachable analog from an unprivileged client in this codebase is `WorkflowMetadataHandler.Authorize`, which implements JWT single-use replay protection for HTTP trigger workflow authorization but performs the "already used" check and the "mark as used" write as two separate, non-atomic steps under different locks.

### Finding Description
`WorkflowMetadataHandler.Authorize` verifies a client-supplied JWT, then checks `h.jwtCache.isReplay(claims.ID)` and — only if the token hasn't been seen and the signer is authorized — calls `h.jwtCache.recordUsage(claims.ID)` at the very end of the function. [1](#0-0) 

The replay cache itself uses two independently-locked operations rather than a single atomic check-and-record: [2](#0-1) 

Because `isReplay` (read lock) and `recordUsage` (separate write lock, invoked only after key-authorization succeeds) are not combined into one atomic operation, two concurrent requests presenting the same JWT can both pass `isReplay` before either calls `recordUsage`, letting the same single-use authorization token be consumed twice. This mirrors the Ambire root cause: the state that is supposed to prevent replay (nonce increment on cancel; JWT `jti` marked used) is not updated atomically with the check that gates the privileged action, so an attacker can race a legitimate authorization to reuse it. In contrast, the vault package's `RequestReplayGuard.CheckAndRecord` in this same codebase performs the check and record atomically under a single mutex, which is the correct pattern. [3](#0-2) 

### Impact Explanation
If exploited, an attacker (or a legitimate client under network jitter) could fire two near-simultaneous requests bearing the same signed JWT for `MethodWorkflowExecute`/HTTP trigger authorization, causing the token to authorize two separate workflow-execution requests instead of one. This is an unprivileged-actor-reachable request-impersonation-adjacent issue that undermines the single-use guarantee that the JWT replay cache is explicitly designed to provide, tested in `TestWorkflowMetadataHandler_Authorize`'s "JWT replay protection" subtest, which only demonstrates sequential (not concurrent) reuse being blocked. [4](#0-3) 

Severity is limited because the window is narrow (microseconds) and requires the attacker to already possess/observe a validly-signed JWT plus win a tight race; the downstream `httpTriggerHandler` also separately rejects duplicate request IDs, which somewhat mitigates duplicate-execution impact for identical requests but not for two distinct request IDs signed together. [5](#0-4) 

### Likelihood Explanation
Low-to-medium likelihood: exploitation requires precise timing to win the TOCTOU race window between `isReplay` and `recordUsage`, and the attacker must already control or intercept a validly signed JWT (which is itself gated by workflow owner signature authorization). It is not a trivial, always-reproducible bypass, but it is a real correctness gap in an unprivileged-facing gateway authorization path.

### Recommendation
Make the JWT replay check atomic: combine `isReplay` and `recordUsage` into a single method (e.g., `checkAndRecord(jti)`) that acquires one lock, checks for existing usage, and if absent, records it before releasing the lock — mirroring `RequestReplayGuard.CheckAndRecord` in `core/capabilities/vault/request_replay_guard.go`. This should be done in `workflow_metadata_handler.go`'s `jwtReplayCache` and should be called immediately after JWT signature verification succeeds (or immediately once the caller decides to accept the token), not deferred until after all other authorization checks.

### Proof of Concept
1. Client signs one JWT for a `MethodWorkflowExecute` request (`jti = X`) for a workflow it is authorized to execute (per `utils.CreateRequestJWT` in the test helpers).
2. Client fires two goroutines simultaneously, each calling `WorkflowMetadataHandler.Authorize(workflowID, tokenString, req)` with the same token.
3. Both goroutines execute `h.jwtCache.isReplay(claims.ID)` before either has called `h.jwtCache.recordUsage(claims.ID)`, so both observe `exists == false` and proceed to the authorized-keys check, which also succeeds for both.
4. Both goroutines successfully call `recordUsage`, and both return a valid `*gateway.AuthorizedKey`, meaning the same single-use JWT authorized two separate downstream requests instead of one, as illustrated by the existing sequential test in `workflow_metadata_handler_test.go` lines 1193-1217 (which only verifies the second sequential call fails — not concurrent calls).

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L317-355)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```
