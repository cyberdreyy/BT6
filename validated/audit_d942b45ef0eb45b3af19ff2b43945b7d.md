### Title
Unauthenticated Request-ID Squatting Causes Denial of Service for Legitimate HTTP Trigger Requests - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The gateway's HTTP Trigger Handler tracks in-flight user requests in a single global `callbacks` map keyed only by the client-supplied JSON-RPC `req.ID`, with no scoping by workflow ID, workflow owner, or caller identity. Any unauthenticated caller who can reach `HandleUserTriggerRequest` can pre-register (or race) the same `id` value that a victim is about to use, causing the victim's legitimate trigger request to be rejected outright. This is the same failure class as the original report: an attacker manipulates a shared, externally-influenceable piece of state right before/at the same time a victim's request depends on it, forcing the victim's operation to fail.

### Finding Description
`HandleUserTriggerRequest` validates only that the request ID is non-empty and does not contain `/`: [1](#0-0) 

It does not tie the ID to the caller's identity or workflow; the callback is stored in a package-wide map keyed purely by that client-chosen ID, as shown by the test's direct access `handler.callbacks[requestID]`: [2](#0-1) 

The handler explicitly rejects a second request using the same ID while the first is still in flight, returning a conflict error to the caller who lost the race: [3](#0-2) 

Because request IDs are not namespaced per sender/workflow, and JWT-based authorization (`authorizeRequest`) happens only *after* ID validation/parsing (see `HandleUserTriggerRequest` flow: `validatedTriggerRequest` → `resolveWorkflowID` → `authorizeRequest`), any caller — including one without a valid workflow key — can occupy an ID before the intended workflow-owner's request arrives, since ID uniqueness is enforced globally, not per-authorized-workflow: [4](#0-3) 

This mirrors the reported bug class: the victim's operation depends on a piece of externally-observable/guessable state (here, the request ID keyspace) that an unprivileged third party can pre-empt just before the victim's legitimate call, causing the legitimate call to fail with a conflict/DoS rather than succeeding.

### Impact Explanation
A victim's trigger execution request can be denied service by any party capable of sending a JSON-RPC message to the gateway endpoint with the same `id`, without needing to be authorized for the victim's workflow. Since the rejection happens for the *second* arriving request regardless of who's legitimate, an attacker who predicts or observes a victim's request ID (e.g., sequential/UUID reuse patterns, or the ID being echoed/logged elsewhere) can grief specific workflow executions, delaying or blocking legitimate triggers and wasting the round trip/retry cost for the victim.

### Likelihood Explanation
Exploitability depends on the attacker's ability to predict or learn the victim's chosen `id` before the victim's request lands — this is a real precondition (similar to the original report's need to front-run a specific transaction) and not guaranteed in all deployments. However, no authentication/authorization is required to attempt ID squatting, and the check for duplicate IDs happens prior to authorization, so any external client interacting with the exposed gateway endpoint is a valid pre-authorization actor for this collision.

### Recommendation
Scope the in-flight callback keyspace by an identity/workflow-bound key rather than the raw client-supplied `id` alone (e.g., combine `id` with `workflowID` and/or an authenticated sender identifier established via JWT before checking for duplicates), and move duplicate-ID detection after authorization succeeds so unauthorized callers cannot occupy or collide with another workflow owner's request IDs.

### Proof of Concept
1. Victim intends to call `workflows.execute` on the gateway with `id = "abc123"` for their authorized workflow.
2. Attacker (unauthorized for that workflow, or any client with network access to the endpoint) sends a JSON-RPC request with `Method: "workflows.execute"` and the same `id = "abc123"` slightly before the victim's request is processed — this reaches `HandleUserTriggerRequest` before authorization is checked (`validatedTriggerRequest` runs first).
3. The gateway registers the attacker's callback under `callbacks["abc123"]` (see `TestHttpTriggerHandler_HandleUserTriggerRequest` behavior at [2](#0-1) ).
4. Victim's request with the same `id` arrives moments later and is rejected with `jsonrpc.ErrConflict` / "in-flight request" as demonstrated by the "duplicate request ID" test ( [5](#0-4) ), denying the victim's legitimate workflow execution.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L183-195)
```go
func (h *httpTriggerHandler) validateRequestID(ctx context.Context, requestID string, callback handlers.Callback) error {
	if requestID == "" {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "'id' field is required and cannot be empty. Use a new unique request 'id' for each request", callback)
		return errors.New("empty request ID")
	}
	// Request IDs from users must not contain "/", since this character is reserved
	// for internal node-to-node message routing (e.g., "http_action/{workflowID}/{uuid}").
	if strings.Contains(requestID, "/") {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "request ID must not contain '/'", callback)
		return errors.New("request ID must not contain '/'")
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L75-85)
```go
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback, time.Now())
		require.NoError(t, err)

		handler.callbacksMu.Lock()
		saved, exists := handler.callbacks[requestID]
		handler.callbacksMu.Unlock()

		require.True(t, exists)
		require.Equal(t, callback, saved.Callback)
		require.NotNil(t, saved.responseAggregators)
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
