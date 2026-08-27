### Title
Unauthenticated workflowID enumeration oracle via distinguishable "workflow not found" vs "Auth failure" error messages - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`resolveWorkflowID` checks workflow existence via `GetWorkflowReference` before any authentication is performed in `HandleUserTriggerRequest`, returning a distinct "Workflow not found" message when the workflowID does not exist. If the workflowID exists, execution proceeds to `authorizeRequest`, which returns a differently worded "Auth failure: ..." message for missing/invalid JWTs or unauthorized signers. An unauthenticated attacker can use this message difference to determine whether an arbitrary 66-character workflowID is registered, without ever presenting a valid credential.

### Finding Description
In `HandleUserTriggerRequest` [1](#0-0) , `resolveWorkflowID` is called before `authorizeRequest`. Inside `resolveWorkflowID`, if the supplied `workflowID` is not present in `workflowMetadataHandler.workflowIDToRef`, the handler immediately returns the error message `"Workflow not found. 'workflowID' %s is not a valid workflow ID"` [2](#0-1) .

If the workflowID does exist, control passes to `authorizeRequest`, which calls `WorkflowMetadataHandler.Authorize` [3](#0-2) . `Authorize` first verifies the JWT via `utils.VerifyRequestJWT(token, *req)`; if the request has no `Auth` token (or an invalid one), this fails before the internal "workflow ID not found in authorized keys" branch is ever reached [4](#0-3) . The resulting error is wrapped as `"Auth failure: " + err.Error()` [3](#0-2) .

Both cases use the same JSON-RPC error code (`jsonrpc.ErrInvalidRequest`), but the message text differs and is observable by the caller through the gateway's `handleUserError`/`callback.SendResponse` path [5](#0-4) . This is confirmed by existing unit tests, which explicitly assert on the differing substrings: `"workflow not found"` for a nonexistent ID [6](#0-5)  versus `"auth failure"` for an invalid JWT/unauthorized signer against an existing workflow [7](#0-6) .

Since `req.Auth` is entirely attacker-controlled (it can be empty or garbage) and the gateway's `ProcessRequest` routes any well-formed JSON-RPC request straight into `HandleJSONRPCUserMessage` → `HandleUserTriggerRequest` without a prior authentication gate [8](#0-7) , an attacker can send unauthenticated requests, one workflowID at a time, and use the response text to determine which workflowIDs are registered in the system before ever presenting valid credentials.

### Impact Explanation
This is an information-disclosure / minimal-state-exposure issue: it lets an unauthenticated caller enumerate live workflowIDs across all tenants, confirming which candidate IDs correspond to real, registered workflows. This does not by itself expose secrets, keys, or allow request forgery, but it narrows the search space for targeted attacks (e.g., subsequent JWT signer brute-forcing/social engineering against a known-live workflow, or correlating workflowIDs with off-chain intelligence about specific owners). It matches the "minimal information disclosure pre-auth" class rather than a direct authentication/authorization bypass.

### Likelihood Explanation
Fully unauthenticated and trivially repeatable: no credentials, roles, or prior state are required. The attacker only needs to submit well-formed `workflows.execute` JSON-RPC requests with candidate `workflowID` values and an empty/garbage `Auth` field, then inspect the returned message text. This can be scripted and repeated for many candidate IDs.

### Recommendation
Return a uniform error message and code for both "workflow not found" and "authorization failed" cases when observed from an unauthenticated/invalid-credential context, e.g. always respond with a generic `"Auth failure"` or `"Not found"` regardless of whether the workflowID exists, and perform JWT signature verification (or at minimum presence-of-Auth check) before existence lookup so that no branch reveals workflow existence pre-authentication. Ensure error messages for missing-workflow and unauthorized-signer cases in `resolveWorkflowID`/`authorizeRequest` are textually and semantically indistinguishable.

### Proof of Concept
Go table test in `http_trigger_handler_test.go`:
1. Register one workflow (`workflowID`) with an authorized signer key, as done in `TestHttpTriggerHandler_HandleUserTriggerRequest_JWTAuthorization`.
2. Case A: send `HandleUserTriggerRequest` with `req.Auth = ""` (no token) and `Workflow.WorkflowID` set to a random, unregistered 66-char hex ID. Capture the callback's `RawResponse` message.
3. Case B: send `HandleUserTriggerRequest` with `req.Auth = ""` (no token) and `Workflow.WorkflowID` set to the registered `workflowID`. Capture the callback's `RawResponse` message.
4. Assert that Case A's message contains `"Workflow not found"` while Case B's message contains `"Auth failure"` — i.e., the messages are NOT identical, proving that an unauthenticated caller can distinguish "ID doesn't exist" from "ID exists but not authorized" purely from response content, both under `jsonrpc.ErrInvalidRequest`.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-102)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L335-346)
```go
func (h *httpTriggerHandler) resolveWorkflowID(ctx context.Context, triggerReq *jsonrpc.Request[gateway_common.HTTPTriggerRequest], requestID string, callback handlers.Callback) (string, error) {
	h.lggr.Debugw("resolving workflow ID", "workflowID", triggerReq.Params.Workflow.WorkflowID, "workflowOwner", triggerReq.Params.Workflow.WorkflowOwner, "workflowName", triggerReq.Params.Workflow.WorkflowName, "workflowTag", triggerReq.Params.Workflow.WorkflowTag, "requestID", requestID)
	workflowID := triggerReq.Params.Workflow.WorkflowID
	if workflowID != "" {
		workflowID = normalizeHex(workflowID, workflowIDLength)
		_, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
		if !found {
			h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, fmt.Sprintf("Workflow not found. 'workflowID' %s is not a valid workflow ID", workflowID), callback)
			return "", errors.New("workflow not found")
		}
		return workflowID, nil
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L560-590)
```go
func (h *httpTriggerHandler) handleUserError(ctx context.Context, requestID string, code int64, message string, callback handlers.Callback) {
	switch code {
	case jsonrpc.ErrInternal, jsonrpc.ErrServerOverloaded, jsonrpc.ErrUnknown, jsonrpc.ErrLimitExceeded, jsonrpc.ErrConflict:
		h.lggr.Errorw("returning error to user", "code", code, "message", message, "requestID", requestID)
	default:
		h.lggr.Warnw("returning error to user", "code", code, "message", message, "requestID", requestID)
	}
	resp := &jsonrpc.Response[json.RawMessage]{
		Version: "2.0",
		ID:      requestID,
		Error: &jsonrpc.WireError{
			Code:    code,
			Message: message,
		},
	}
	rawResp, err := json.Marshal(resp)
	if err != nil {
		h.lggr.Errorw("failed to marshal error response", "err", err, "requestID", requestID)
		return
	}
	errorCode := api.FromJSONRPCErrorCode(code)
	h.metrics.IncrementRequestErrors(ctx, code, h.lggr)
	err = callback.SendResponse(handlers.UserCallbackPayload{
		RawResponse: rawResp,
		ErrorCode:   errorCode,
	})
	if err != nil {
		h.lggr.Errorw("failed to send user callback", "err", err, "requestID", requestID)
		return
	}
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-96)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1062-1113)
```go
	t.Run("invalid JWT token", func(t *testing.T) {
		callback := hc.NewCallback()

		triggerReq := createTestTriggerRequest(workflowID)
		reqBytes, err2 := json.Marshal(triggerReq)
		require.NoError(t, err2)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-2",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
			Auth:    "invalid.jwt.token",
		}

		err = handler.HandleUserTriggerRequest(ctx, req, callback, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "auth failure")

		r, err2 := callback.Wait(t.Context())
		require.NoError(t, err2)
		requireUserErrorSent(t, r, jsonrpc.ErrInvalidRequest)
	})

	t.Run("unauthorized signer", func(t *testing.T) {
		callback := hc.NewCallback()
		unauthorizedKey := createTestPrivateKey(t)

		triggerReq := createTestTriggerRequest(workflowID)
		reqBytes, err2 := json.Marshal(triggerReq)
		require.NoError(t, err2)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-3",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}

		jwtToken := createTestJWTToken(t, req, unauthorizedKey)
		req.Auth = jwtToken

		err = handler.HandleUserTriggerRequest(ctx, req, callback, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "auth failure")

		r, err2 := callback.Wait(t.Context())
		require.NoError(t, err2)
		requireUserErrorSent(t, r, jsonrpc.ErrInvalidRequest)
	})
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1115-1145)
```go
	t.Run("workflow not found", func(t *testing.T) {
		callback := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err2 := json.Marshal(triggerReq)
		require.NoError(t, err2)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-4",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}

		jwtToken := createTestJWTToken(t, req, privateKey)
		req.Auth = jwtToken

		err = handler.HandleUserTriggerRequest(ctx, req, callback, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "workflow not found")

		r, err2 := callback.Wait(t.Context())
		require.NoError(t, err2)
		requireUserErrorSent(t, r, jsonrpc.ErrInvalidRequest)
	})
```

**File:** core/services/gateway/gateway.go (L264-277)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

```
