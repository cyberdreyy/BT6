### Title
Global (unscoped) `requestID` map in HTTP trigger handler lets one workflow owner front-run and block another owner's trigger requests - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The gateway's HTTP trigger handler tracks in-flight requests in a single map keyed **only by the client-supplied JSON-RPC `id`** (`requestID`), with no scoping by workflow ID or workflow owner. Because `id` is fully attacker-chosen and the uniqueness check happens globally across all workflows, any authorized workflow owner can pre-claim a `requestID` string before another (unrelated) workflow owner's legitimate request with the same `id` arrives, causing the victim's request to be rejected with a conflict error. This is the same root-cause pattern as the referenced Nouns Builder finding: a globally-unique identifier derived purely from client-supplied/request data, without a per-account/per-namespace discriminator, enables front-running-based denial of a legitimate actor's request.

### Finding Description
`httpTriggerHandler.setupCallback` stores pending requests in `h.callbacks map[string]savedCallback`, keyed by the raw `requestID` taken directly from the incoming JSON-RPC request `id` field: [1](#0-0) 

```go
func (h *httpTriggerHandler) setupCallback(...) {
    h.callbacksMu.Lock()
    defer h.callbacksMu.Unlock()

    if _, found := h.callbacks[requestID]; found {
        h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, ...)
        return nil, fmt.Errorf("in-flight request ID: %s", requestID)
    }
```

`requestID` is `req.ID`, validated only for non-emptiness and absence of `/` in `validateRequestID`; it is never namespaced by `workflowID` or `workflowOwner`: [2](#0-1) 

The check for duplication (`setupCallback`) happens *after* `authorizeRequest` and `checkRateLimit` succeed for the *current* request's own workflow — i.e., each caller only needs a valid JWT for *their own* workflow, not the victim's: [3](#0-2) 

Because the map is global (not `map[workflowID]map[requestID]...` or similar), a request `id` value is a single shared namespace across all workflow owners on the gateway/DON shard. This mirrors the Nouns Builder bug where `proposalId = hash(targets, values, calldatas, descriptionHash)` was globally unique without a per-proposer nonce, letting any actor block another's proposal by claiming the same id first.

### Impact Explanation
An attacker who legitimately owns/operates any registered workflow (an "unprivileged" caller relative to the victim's workflow) can observe or guess a victim's chosen `id` (e.g., if IDs are predictable, sequential, or reused/retried by client tooling) and submit their own authorized trigger request using that same `id` first. The victim's subsequent legitimate request is rejected via `jsonrpc.ErrConflict` ("has already been used... Ensure the requestID is unique") — denying that specific job trigger. Once the attacker's own callback resolves (success/timeout), the slot frees up, allowing repeated targeting, similar to how `Governor.cancel` freed the `proposalId` for repeat griefing. This is a cross-workflow-owner denial-of-service against job execution requests, not merely local to a single workflow — a caller with no privilege over the victim's workflow can interfere with the victim's request lifecycle purely by colliding on the client-chosen `id`.

### Likelihood Explanation
Exploitability depends on: (1) attacker having their own valid, authorized workflow (a low bar - any registered workflow), and (2) attacker being able to predict or race the victim's `id`. If client tooling uses predictable IDs (timestamps, counters, UUIDs generated with weak entropy, or client-side retries reusing the same ID), collision is straightforward. Even with random UUIDs, a targeted attacker monitoring gateway traffic patterns or retry behavior could still race specific high-value `id`s. This is assessed as a real but moderate-likelihood issue, gated by the lack of namespacing being an unambiguous design gap rather than requiring any privileged access.

### Recommendation
Scope the in-flight request tracking key by `(workflowID or workflowOwner, requestID)` instead of `requestID` alone, e.g. change `h.callbacks map[string]savedCallback` to be keyed by a composite key such as `workflowOwner + "/" + requestID` (mirroring the internal reserved `/`-delimited routing scheme already used elsewhere in the codebase). This ensures a client-supplied `id` is only unique within its own workflow/owner namespace and cannot be used to interfere with another owner's requests, analogous to adding a per-account nonce/discriminator to the Governor proposal ID.

### Proof of Concept
1. Victim registers/operates Workflow A (owner `0xVictim`) and prepares to send `HandleUserTriggerRequest` with `id = "job-42"`.
2. Attacker independently registers/operates their own Workflow B (owner `0xAttacker`, fully authorized to trigger it).
3. Attacker races ahead and sends a `workflows.execute` request to Workflow B with `id = "job-42"`. It passes `authorizeRequest`/`checkRateLimit` for Workflow B and is inserted into `h.callbacks["job-42"]` in `setupCallback`.
4. Victim's request for Workflow A with the same `id = "job-42"` arrives at `setupCallback`, finds `h.callbacks["job-42"]` already populated, and is rejected with `jsonrpc.ErrConflict` / "requestID: job-42 has already been used", as reproduced by the existing test `TestHttpTriggerHandler_HandleUserTriggerRequest/duplicate_request_ID`: [4](#0-3) 
5. Once the attacker's own request resolves and the entry is removed, the attacker can repeat the attack for the next attempt by the victim, sustaining the denial of that specific job trigger.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-140)
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
