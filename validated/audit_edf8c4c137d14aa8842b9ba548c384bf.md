## Analog Found

### Title
Global (non-owner-scoped) request-ID collision allows unprivileged users to DoS each other's Gateway trigger/relay requests - (File: `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`, `core/services/gateway/handlers/confidentialrelay/handler.go`)

### Summary
The external report describes a griefing DoS where an attacker front-runs a victim's user-chosen, non-namespaced identifier (`loanId`) so the victim's legitimate operation collides and is rejected. The Chainlink Gateway has the same bug-class: several Gateway handlers de-duplicate in-flight requests using a map keyed **only by the client-supplied `req.ID` string**, with no per-owner/per-workflow namespacing, so any unprivileged client can pre-occupy an ID and cause another unprivileged user's legitimately-authorized request to be rejected.

### Finding Description
In `httpTriggerHandler.setupCallback`, in-flight requests are tracked in a single global map keyed by the raw request ID: [1](#0-0) 

The only validation performed on `req.ID` is that it is non-empty and does not contain `/`: [2](#0-1) 

Crucially, this uniqueness check is **global across all workflows and all users** — it is not scoped by `workflowID` or by the authenticated key/owner, even though `authorizeRequest` (which validates a JWT bound to a specific workflow) runs earlier in the same flow: [3](#0-2) 

Because any unprivileged client that owns/controls *any* registered workflow can generate a validly-signed JWT for that workflow, they can submit a request using an arbitrary or predictable `req.ID` (e.g., a common counter value, timestamp, or an ID they observed being used by another party) *before* the intended legitimate request for a **different** workflow with the same textual ID arrives. The second request fails outright: [4](#0-3) 

The same unscoped pattern exists in the confidential relay handler, where `activeRequests` is keyed purely by `req.ID` with no owner prefix: [5](#0-4) [6](#0-5) 

This is directly analogous to the `loanId` collision bug: the codebase itself demonstrates awareness of, and the correct fix for, this exact bug class in the vault handler, where the request ID is deliberately prefixed with the authorized owner **before** being used as a dedup/lookup key, preventing cross-owner collisions: [7](#0-6) [8](#0-7) 

The HTTP trigger handler and confidential relay handler do not apply this same owner-scoping fix to their in-flight-request dedup maps.

### Impact Explanation
An unprivileged Gateway client (anyone able to register/own a workflow and obtain a valid signature for it) can grief unrelated, unprivileged users by squatting on likely/observed request IDs across the whole Gateway instance. Victim requests using the same `req.ID` value for a completely different workflow are rejected with `ErrConflict`/"in-flight request" errors, denying service without requiring any privileged access — matching the report's "Griefing" impact classification.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to guess or observe the request ID a victim's client will use. Many SDKs/integrations use simple, low-entropy, or sequential IDs (e.g., "1", incrementing counters, or workflow-execution-derived values), which increases collision predictability. No special privileges beyond normal Gateway client access (owning a workflow with a valid signing key) are required to mount the attack.

### Recommendation
Scope the in-flight request dedup key by workflow/owner (or by the authenticated key), not by the raw client-supplied `req.ID` alone — following the same pattern already implemented in the vault handler's owner-prefixed request ID (`AuthorizedOwner() + Separator + req.ID`). Apply this to `httpTriggerHandler.callbacks` and `confidentialrelay` `activeRequests` maps.

### Proof of Concept
1. Attacker registers/owns Workflow A and obtains a valid JWT for `req.ID = "1"` on Workflow A; sends it to `HandleUserTriggerRequest`, which succeeds and inserts `callbacks["1"]`.
2. Before attacker's request completes/expires, a legitimate, unrelated user submits a validly-authorized request for Workflow B also using `req.ID = "1"`.
3. `setupCallback` finds `"1"` already present in the global map and returns `ErrConflict`, denying the legitimate user's request — as reproduced in the existing test `TestHttpTriggerHandler_HandleUserTriggerRequest/duplicate_request_ID`. [4](#0-3)

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-139)
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

	strippedWorkflowID := strings.TrimPrefix(workflowID, "0x")
	legacyExecutionID, err := workflows.EncodeExecutionID(strippedWorkflowID, req.ID) //nolint:staticcheck // legacy ID kept for observability comparison
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID: " + err.Error())
	}
	// Workflows shouldn't use more than one HTTP trigger. If we ever need to support multiple triggers, we'd need to pass
	// trigger index to the Gateway handler and somehow allow senders to pick. For now, we use trigger index 0.
	// Execution IDs here are used only for logging.
	executionIDWithTriggerIndex, err := workflows.GenerateExecutionIDWithTriggerIndex(strippedWorkflowID, req.ID, 0)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID with trigger index: " + err.Error())
	}
	h.lggr.Debugw("processing request",
		"legacyExecutionID", legacyExecutionID,
		"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
		"requestID", req.ID,
		"workflowID", workflowID)

	reqWithKey, err := reqWithAuthorizedKey(triggerReq, *key)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error marshaling trigger request: " + err.Error())
	}

	doneCh, err := h.setupCallback(ctx, req.ID, callback, requestStartTime, workflowID)
	if err != nil {
		return err
	}

	return h.sendWithRetries(ctx, legacyExecutionID, executionIDWithTriggerIndex, reqWithKey, workflowID, doneCh)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L240-248)
```go
	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L711-716)
```go
		expectedRequestID := owner + vaulttypes.RequestIDSeparator + requestID
		response := jsonrpc.Response[json.RawMessage]{
			ID:     expectedRequestID,
			Result: (*json.RawMessage)(&resultBytes),
			Method: vaulttypes.MethodSecretsList,
		}
```
