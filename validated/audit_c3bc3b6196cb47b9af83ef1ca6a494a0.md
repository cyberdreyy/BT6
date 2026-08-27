### Title
Non-atomic JWT replay check-and-record in `WorkflowMetadataHandler.Authorize` allows concurrent duplicate execution from a single JWT - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` prevents JWT replay by calling `jwtCache.isReplay(jti)` and, later, `jwtCache.recordUsage(jti)` as two separate, non-atomic critical sections. Two concurrent calls to `Authorize` carrying the identical token can both pass the `isReplay` check before either reaches `recordUsage`, letting the same JWT authorize two capability executions instead of the intended one.

### Finding Description
`Authorize` verifies the JWT, then performs the replay check and the "mark as used" write as two independent locked operations instead of one atomic check-and-set: [1](#0-0) 

`isReplay` takes an `RLock`, reads the map, and releases the lock; `recordUsage` separately takes a `Lock` and writes the map: [2](#0-1) 

Because there is a window between the `isReplay` read and the `recordUsage` write (during which the workflow-ID/signer lookup at lines 92-104 also executes), two goroutines calling `Authorize(workflowID, token, req)` with the same `jti` concurrently can both observe `exists == false` in `isReplay` and both proceed to `recordUsage`, resulting in two successful authorizations from one JWT. This reaches the network via `httpTriggerHandler.HandleUserTriggerRequest` → `authorizeRequest` → `WorkflowMetadataHandler.Authorize`: [3](#0-2) 

The existing test suite only validates *sequential* replay rejection (`t.Run("JWT replay protection" ...)` and `t.Run("duplicate JWT token and request ID" ...)`), not concurrent submission, so the atomicity gap is untested: [4](#0-3) [5](#0-4) 

Note that reaching this race still requires possession of the exact signed JWT (valid signature by an authorized key for the target workflow, per `utils.VerifyRequestJWT`) — the check-and-act gap does not itself let an attacker forge or upgrade credentials; it only breaks the "one JWT authorizes exactly one execution" (`REQUEST_BINDING`) guarantee once a valid token is already replayable/observable and raced before either the legitimate call or `recordUsage` completes.

### Impact Explanation
If exploited, a single valid signed JWT can trigger two concurrent capability/workflow executions instead of one, doubling resource consumption/execution charges against the workflow the JWT authorizes. This matches a duplicate/unauthorized-execution class impact (billing/resource abuse via violated per-request idempotency), but it does not grant privilege escalation, credential disclosure, or cross-user data exposure — the blast radius is limited to duplicating an execution that the token holder was already authorized to perform once.

### Likelihood Explanation
Exploitation requires: (1) an already-valid, correctly signed JWT for an authorized workflow signer, and (2) the ability to submit that exact token to the gateway twice within the same narrow race window (microseconds, between the `isReplay` read and `recordUsage` write). Obtaining a token that is not one's own generally requires interception/leakage outside of application logic (out of scope per audit rules for network-layer paths); a legitimate token holder replaying their own token merely duplicates their own execution rather than attacking another user. The race window itself is narrow and requires precise timing, making unattended/reliable exploitation nontrivial, though technically reproducible with deliberate concurrent requests.

### Recommendation
Make the JWT check-and-record operation atomic: acquire a single `Lock()` in the cache and perform "check exists, then insert" under that one critical section (i.e., an atomic `checkAndRecord(jti) bool` method) instead of separate `isReplay`/`recordUsage` calls, and call this atomic method immediately after signature verification in `Authorize` before doing the authorized-key lookup.

### Proof of Concept
Go test plan (table/concurrency test) for `jwt_replay_cache` / `WorkflowMetadataHandler.Authorize`:
1. Construct a `WorkflowMetadataHandler` with one registered workflow/authorized key (as in `TestWorkflowMetadataHandler_Authorize`).
2. Create a single signed JWT (`token`) with jti `X` bound to the request.
3. Launch two goroutines that both call `handler.Authorize(workflowID, token, req)` concurrently (e.g., synchronized with a `sync.WaitGroup` and a start barrier channel to maximize overlap).
4. Collect both results; assert that exactly one call returns `(key, nil)` and the other returns `(nil, err)` with `"JWT token has already been used"`.
5. Run with `-race` and repeat in a loop (e.g., 1000 iterations) to demonstrate that, under the current split `isReplay`/`recordUsage` implementation, both calls can occasionally succeed (test flakes/fails), whereas after the atomic-check-and-set fix, the assertion holds deterministically across all iterations.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-369)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L357-394)
```go
	t.Run("duplicate JWT token and request ID", func(t *testing.T) {
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
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "token has already been used")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrInvalidRequest)
	})
```
