## Analog Found

### Title
HTTP trigger gateway request-ID namespace is shared across all workflows, allowing griefing/DoS via ID squatting - (File: `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`)

### Summary
The reported bug is a classic **user-supplied-identifier griefing** pattern: a value chosen by the caller (`accountId`) is stored in a single global namespace with "first write wins" semantics, so any unprivileged actor who can observe/predict that value can pre-claim it and permanently block the legitimate owner from using the protocol. The chainlink CRE HTTP-trigger gateway handler has the same structural pattern: JSON-RPC `req.ID` (the "request ID") is used as the sole key in a single, gateway-wide `callbacks` map that is **not scoped to a workflow, workflow owner, or sender** — only global uniqueness is enforced.

### Finding Description
`httpTriggerHandler.HandleUserTriggerRequest` processes an unprivileged, internet-facing user request through the gateway: [1](#0-0) 

Authorization (`authorizeRequest`) validates the caller's JWT against the **target workflow's** authorized-signer set only — it says nothing about the caller's right to a particular `req.ID`: [2](#0-1) 

After authorization, `setupCallback` reserves the request slot keyed **only by `requestID`**, in a map that is global to the gateway instance (shared by every workflow/DON the gateway serves), and rejects the call if that key is already taken: [3](#0-2) 

This is confirmed by the "duplicate request ID" test, which shows a second request with the same `req.ID` is rejected with `jsonrpc.ErrConflict`, regardless of which workflow it targets: [4](#0-3) 

Because `req.ID` is fully attacker-controlled (any authenticated caller of *any* workflow served by the gateway can set it to an arbitrary string), and the uniqueness check spans **all workflows** rather than being scoped per-workflow/per-owner, a caller who is authorized only for their own (unrelated) workflow A can pre-register a callback using a `req.ID` that a victim's client for workflow B is about to use. The victim's genuine request for workflow B is then rejected outright with "requestID: %s has already been used", exactly mirroring the on-chain bug where `AccountManager.createAccount` rejects legitimate account creation because a front-runner already claimed the same `accountId` in a single shared mapping.

### Impact Explanation
This is a griefing/DoS vector with no profit motive required for the attacker, matching the reported impact category:
- Any caller who can obtain a valid JWT for *some* workflow on a shared gateway (which is a normal, unprivileged, internet-facing operation) can block execution of a *different* workflow's legitimate request merely by claiming the same `req.ID` first.
- The victim's request fails with a JSON-RPC conflict error and no execution occurs, denying service for the duration until cleanup, and repeatable indefinitely if the attacker can predict/observe the ID pattern used by the victim's client (e.g., sequential IDs, timestamp-based IDs, or IDs echoed by application logs/telemetry).
- Because the check happens in `setupCallback`, after `authorizeRequest`, it is reachable by any client that is a legitimate, unprivileged caller of *some* workflow served by that gateway — not a gateway operator or node.

### Likelihood Explanation
Exploitability depends on the attacker being able to guess or observe the `req.ID` the victim's client will use before the victim's own request lands. Given the README explicitly documents "User Requests: Plain string identifiers" with only a "cannot contain '/'" constraint, many client implementations are likely to use predictable schemes (incrementing counters, UUIDs derived from deterministic inputs, timestamps), making pre-claiming practical in shared/multi-tenant gateway deployments. The barrier to becoming an "authorized" attacker is low — obtaining a valid JWT for any single workflow on the shared gateway is enough, since the collision check is not scoped by workflow/owner.

### Recommendation
Scope the request-ID uniqueness/callback map by `(workflowID, requestID)` or `(workflowOwner, requestID)` instead of a single global `requestID` key, so that ID collisions can only occur within the same workflow/tenant that legitimately controls that namespace — analogous to the suggested fix of using a monotonically-incrementing or owner-scoped identifier instead of an arbitrary global user-supplied value.

### Proof of Concept
1. Attacker obtains a valid signing key/JWT for Workflow A (any workflow they are legitimately authorized to call).
2. Attacker observes or predicts the `req.ID` that a victim's client will use for a call to unrelated Workflow B (e.g., a sequential or timestamp-derived ID).
3. Attacker sends `HandleUserTriggerRequest` for Workflow A using that same `req.ID` just before the victim's request arrives, causing `setupCallback` to register the ID first: [5](#0-4) 
4. Victim's legitimate request for Workflow B arrives with the same `req.ID` and is rejected with `jsonrpc.ErrConflict` ("requestID ... has already been used"), even though Workflow B has nothing to do with Workflow A or the attacker.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-107)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-434)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}

	// Build one response aggregator per shard the workflow is assigned to.
	assigned := h.workflowMetadataHandler.WorkflowShards(workflowID)
	if len(assigned) == 0 {
		// this shouldn't happen because we checked it in authorizeRequest()
		h.handleUserError(ctx, requestID, jsonrpc.ErrInternal, fmt.Sprintf("Workflow %s is not assigned to any DONs", workflowID), callback)
		return nil, errors.New("workflow is not assigned to any shards")
	}

	aggregators := make(map[string]*aggregation.IdenticalNodeResponseAggregator, len(assigned))
	for _, shard := range assigned {
		// (N+F)//2 + 1 threshold where N = number of nodes, F = number of faulty nodes
		threshold := (len(shard.members)+shard.f)/2 + 1
		agg, err := aggregation.NewIdenticalNodeResponseAggregator(threshold)
		if err != nil {
			return nil, errors.New("failed to create response aggregator: " + err.Error())
		}
		aggregators[shard.donID] = agg
	}

	doneCh := make(chan struct{})
	h.callbacks[requestID] = savedCallback{
		Callback:            callback,
		requestStartTime:    requestStartTime,
		createdAt:           time.Now(),
		responseAggregators: aggregators,
		doneCh:              doneCh,
	}
	return doneCh, nil
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
