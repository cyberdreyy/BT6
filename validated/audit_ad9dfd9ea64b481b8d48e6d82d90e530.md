## Analysis

Confirmed: `HandleJSONRPCUserMessage` (`core/services/gateway/handlers/capabilities/v2/http_handler.go`) directly calls `HandleUserTriggerRequest`, which calls `validatedTriggerRequest` → `resolveWorkflowID` → `authorizeRequest`, in that order, with no authentication check preceding `resolveWorkflowID`. [1](#0-0) [2](#0-1) 

### Title
Unauthenticated workflow existence enumeration via `resolveWorkflowID` running before `authorizeRequest` - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`httpTriggerHandler.HandleUserTriggerRequest` resolves the `workflowOwner`/`workflowName`/`workflowTag` (or `workflowID`) triple against the metadata cache via `resolveWorkflowID` before any JWT/`Auth` verification occurs in `authorizeRequest`. An unauthenticated client can send a `workflows.execute` JSON-RPC request with no `Auth` field and distinguish "workflow not found" (`ErrInvalidRequest`, "Workflow not found...") from "workflow exists but requires auth" (proceeds to `authorizeRequest`, which fails differently), enabling enumeration of valid workflow selector triples.

### Finding Description
In `HandleUserTriggerRequest`, `resolveWorkflowID` is called at line 94, before `authorizeRequest` at line 99: [2](#0-1) 

`resolveWorkflowID` looks up `h.workflowMetadataHandler.GetWorkflowID(workflowOwner, workflowName, workflowTag)` purely from request-supplied, unauthenticated fields and returns a distinct error ("Workflow not found. Provide either a valid 'workflowID' or a valid combination of ...") when the triple doesn't match any known workflow: [3](#0-2) 

If the triple *does* resolve to a workflow ID, execution proceeds into `authorizeRequest`, which calls `workflowMetadataHandler.Authorize(workflowID, req.Auth, req)`. With `req.Auth` empty/invalid, `utils.VerifyRequestJWT` fails and a *different* error ("Auth failure: ...") is returned: [4](#0-3) [5](#0-4) 

There is no gate that requires `req.Auth` to be present/valid before the workflow-selector lookup runs. `validatedTriggerRequest` only checks JSON parsing, request ID format, method name, and field-length/format validity of the selector fields — none of which require credentials: [6](#0-5) 

An attacker with no credential at all can therefore send repeated `workflows.execute` requests, varying `workflowOwner`/`workflowName`/`workflowTag` (or `workflowID`), and use the differing error code/message ("Workflow not found..." vs. "Auth failure: ...") to determine whether a given owner+name+tag triple (or workflow ID) corresponds to a real registered workflow, before any signature/JWT check is performed.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated party can enumerate which `(workflowOwner, workflowName, workflowTag)` triples — or raw `workflowID`s — correspond to real, registered workflows on the gateway. It does not by itself allow authentication bypass, execution of the workflow, or credential/fund compromise, since `authorizeRequest`/`Authorize` still correctly rejects the request due to missing/invalid JWT before any execution occurs (`sendWithRetries` is never reached). The impact is limited to existence/metadata disclosure (workflow registry enumeration), not a "real node compromise" class (no privilege escalation, no unauthorized job run, no fund movement, no secret disclosure of authorized-key material).

### Likelihood Explanation
Trivial to exploit and fully repeatable: no credentials, JWT, or prior registration are needed — a plain unauthenticated POST with distinct field permutations is sufficient to probe existence. The only "cost" is brute-forcing owner/name/tag combinations, which is feasible for known/guessed workflow owners and short/predictable workflow names/tags.

### Recommendation
Reorder the checks in `HandleUserTriggerRequest` so `authorizeRequest`-style credential verification (at minimum, JWT signature validity independent of the specific `workflowID`) happens before, or is fused with, `resolveWorkflowID`, and ensure error responses for "unknown workflow" and "invalid/missing auth" are indistinguishable (same error code and generic message) to unauthenticated callers. Alternatively, verify the JWT signature/structure first (without requiring it to map to a specific authorized key yet), and only differentiate "not found" vs. "not authorized" after confirming the request carries a validly-signed credential.

### Proof of Concept
Go handler-level test plan (extending `http_trigger_handler_test.go`):
1. Construct an `httpTriggerHandler` with a `WorkflowMetadataHandler` pre-populated (via `syncMetadata`/internal state) with one known workflow reference/ID and authorized key.
2. Case A: Send `jsonrpc.Request` with `Method: "workflows.execute"`, valid JSON params containing a **non-existent** `workflowOwner`/`workflowName`/`workflowTag`, and `Auth: ""` (no credential). Call `HandleUserTriggerRequest`. Assert the callback receives an error with message containing "Workflow not found" and code `jsonrpc.ErrInvalidRequest`.
3. Case B: Send an otherwise identical request but with the **existing** `workflowOwner`/`workflowName`/`workflowTag` and still `Auth: ""`. Call `HandleUserTriggerRequest`. Assert the callback receives a *different* error message ("Auth failure: ...") for the same `jsonrpc.ErrInvalidRequest` code.
4. Assert that these two responses are distinguishable (different `Message` strings) purely based on unauthenticated selector guessing, proving `resolveWorkflowID` executes and leaks existence before `authorizeRequest`/JWT verification meaningfully gates access.
5. (Optional hardening test) After the fix, assert both cases return an identical generic error message/code regardless of whether the workflow selector exists.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L391-401)
```go
func (h *gatewayHandler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback handlers.Callback) error {
	h.metrics.IncrementTriggerRequestCount(ctx, h.lggr)
	err := h.triggerHandler.HandleUserTriggerRequest(ctx, &req, callback, time.Now())
	if err != nil {
		h.lggr.Errorw("failed to handle user trigger request", "requestID",
			req.ID, "err", err)
		// error response is sent to the response channel by the trigger handler
		// so return nil after logging
	}
	return nil
}
```

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L142-171)
```go
func (h *httpTriggerHandler) validatedTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*jsonrpc.Request[gateway_common.HTTPTriggerRequest], error) {
	if req.Params == nil {
		h.handleUserError(ctx, "", jsonrpc.ErrInvalidRequest, "'params' field is missing. Include a valid 'params' object", callback)
		return nil, errors.New("request params is nil")
	}

	triggerReq, err := h.parseTriggerRequest(ctx, req, callback)
	if err != nil {
		return nil, err
	}

	if err := h.validateRequestID(ctx, req.ID, callback); err != nil {
		return nil, err
	}

	if err := h.validateMethod(ctx, req.Method, req.ID, callback); err != nil {
		return nil, err
	}

	if err := h.validateTriggerParams(ctx, triggerReq, req.ID, callback); err != nil {
		return nil, err
	}

	return &jsonrpc.Request[gateway_common.HTTPTriggerRequest]{
		Version: req.Version,
		ID:      req.ID,
		Method:  gateway_common.MethodWorkflowExecute,
		Params:  triggerReq,
	}, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L335-359)
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
	workflowOwner := normalizeHex(triggerReq.Params.Workflow.WorkflowOwner, workflowOwnerLength)
	workflowName := "0x" + hex.EncodeToString([]byte(workflows.HashTruncateName(triggerReq.Params.Workflow.WorkflowName)))
	workflowID, found := h.workflowMetadataHandler.GetWorkflowID(
		workflowOwner,
		workflowName,
		triggerReq.Params.Workflow.WorkflowTag,
	)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "Workflow not found. Provide either a valid 'workflowID' or a valid combination of 'workflowOwner', 'workflowName', and 'workflowTag'", callback)
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-90)
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
```
