### Title
`resolveWorkflowID` executes before `authorizeRequest`, allowing unauthenticated workflow-existence disclosure - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Finding Description
In `httpTriggerHandler.HandleUserTriggerRequest`, the call order is: `validatedTriggerRequest` → `resolveWorkflowID` → `authorizeRequest` → `checkRateLimit`. [1](#0-0) 

`resolveWorkflowID` looks up the workflow by ID (or by owner/name/tag) via `h.workflowMetadataHandler.GetWorkflowReference` / `GetWorkflowID`, and if not found, returns an explicit "Workflow not found" error to the caller — before any credential (`req.Auth`) is checked. [2](#0-1) 

`authorizeRequest`, which performs the actual JWT/signature verification via `h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)`, is only invoked afterward, and only if `resolveWorkflowID` succeeded. [3](#0-2) 

This means an attacker can submit a syntactically valid `workflowID` (or `workflowOwner`/`workflowName`/`workflowTag` triple) with no `Auth` at all, and the distinct "Workflow not found" vs. "Auth failure" error responses reveal whether a given workflow exists, without needing any valid credential.

### Impact Explanation
This is a genuine unauthenticated information-disclosure issue: an attacker can enumerate/confirm the existence of workflows (and, by testing owner/name/tag combinations, confirm specific ownership/naming metadata) purely from the response distinguishing "workflow not found" from "auth failure," with zero credentials. This maps to a low/informational disclosure class (workflow existence, not secrets/keys/funds) — it does not by itself allow bypassing authentication for the actual trigger request, since `authorizeRequest` still runs and blocks any unauthorized workflow execution.

### Likelihood Explanation
Trivially reachable: precondition is none (unauthenticated gateway client), and the request only needs a syntactically valid `workflowID` or owner/name/tag combination plus an empty/invalid `Auth` field. This is repeatable and requires no privileged role.

### Recommendation
Reorder the checks so that request authentication/authorization is validated before revealing whether a workflow exists — e.g., verify `req.Auth`'s signature validity generically first (or make the "not found" and "auth failure" responses indistinguishable), then perform workflow resolution only after credential validity is established, or fold the not-found/auth-failure paths into a single generic error message.

### Proof of Concept
Add a table-driven test in `http_trigger_handler_test.go` that calls `HandleUserTriggerRequest` with:
1. A valid-format `workflowID` that does not exist in `workflowMetadataHandler`, empty `req.Auth`.
2. A valid-format `workflowID` that exists, empty/invalid `req.Auth`.

Assert that in case 1, the callback receives error message "Workflow not found..." (proving pre-auth disclosure), while in case 2 it receives "Auth failure: ...". Then assert (as the fix) that both cases return the same generic error/message regardless of workflow existence, confirming order was corrected to authenticate before resolving.

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
