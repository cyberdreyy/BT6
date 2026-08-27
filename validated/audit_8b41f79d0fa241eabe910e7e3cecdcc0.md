Based on my analysis, this is the key finding:

### Title
Global (non-scoped) `requestID` key in `httpTriggerHandler.callbacks` allows any authorized workflow caller to pre-claim/collide a victim's request ID and cause a denial-of-service — analog of the loanId front-running bug ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
The Gondi report's root cause is that a caller-supplied identifier (`loanId`) is used as the sole key to validate/complete a stateful action, and an unrelated party can front-run and mutate/occupy that key before the legitimate actor's transaction lands, permanently blocking the legitimate flow. The chainlink gateway's HTTP trigger handler has a structurally similar pattern: the `requestID` supplied by the caller is used as a *global* map key (`h.callbacks[requestID]`), not scoped per-workflow or per-owner, and `setupCallback` rejects any request whose ID is already in-flight with `jsonrpc.ErrConflict`.

### Finding Description
`HandleUserTriggerRequest` validates the request, authorizes it against the target workflow (`h.authorizeRequest`), then calls `setupCallback(ctx, req.ID, ...)`: [1](#0-0) 

The `callbacks` map is keyed purely by the client-controlled `requestID` string, with no workflow/owner scoping baked into the key: [2](#0-1) 

Because any authorized caller (owner of any workflow that has valid auth) can pick an arbitrary `requestID` string (only constrained to be non-empty and not contain `/`, see `validateRequestID`), an attacker who can predict or observe a victim's upcoming `requestID` (e.g., sequential IDs, IDs derived from public/observable data, or IDs echoed back by client-side tooling before submission) can submit their own authorized request using that same ID first. This occupies the `callbacks[requestID]` slot until the entry naturally expires or completes, causing the legitimate request to be rejected with `jsonrpc.ErrConflict` ("requestID has already been used") — mirroring how the Gondi attacker rewrites `_loans[loanId]` to invalidate the borrower's transaction validation.

This is analogous but not identical to the confirmed pattern already tested in-repo (`TestHttpTriggerHandler_HandleUserTriggerRequest/duplicate request ID`), which explicitly documents that a second request with the same ID is rejected: [3](#0-2) 

The same collision-key pattern (global map keyed only by client-chosen `req.ID`, no workflow/owner scoping) also exists in the vault and confidential-relay gateway handlers: [4](#0-3) [5](#0-4) 

### Impact Explanation
If request IDs are predictable or observable prior to submission (e.g., client generates IDs deterministically, or IDs are visible via logs/network timing), any other authorized/unprivileged-but-authenticated caller of the gateway can deny service to a specific victim request by squatting the ID slot. Since gateway-fronted flows (workflow execution triggers, vault secret operations, confidential relay) are user/business logic critical, a sustained ID-squatting attack could block execution requests indefinitely, similar to how the Gondi lender could indefinitely block borrower repayment. However, unlike the Gondi case, requestIDs are typically UUIDs generated client-side and not attacker-guessable by default, which significantly limits practical exploitability — this mirrors the original report's initial "low severity" assessment before conditions were found to make it more likely.

### Likelihood Explanation
Likelihood is **low-to-moderate**: exploitation requires the attacker to (a) hold valid authorization for some workflow/vault-owner context (not necessarily the victim's) and (b) know or predict the victim's `requestID` before the victim's request reaches the gateway, which is not the default operating mode (client SDKs use random UUIDs). No evidence was found in the codebase of any mechanism that scopes the `callbacks`/`activeRequests` map by sender/owner, which is the only structural fix that would eliminate the class entirely.

### Recommendation
Scope in-flight-request tracking by `(sender/owner, requestID)` rather than by `requestID` alone, mirroring the mitigation applied to the original Gondi finding (restricting mutation to legitimate/authorized parties on their own identifiers). Concretely, change `httpTriggerHandler.callbacks`, `vault/handler.go` `activeRequests`, and `confidentialrelay/handler.go` `activeRequests` to key on a composite of the authorized owner (already available post-authorization) and `req.ID`, so that a collision can only occur within the same authorized owner's namespace, not across owners.

### Proof of Concept
Conceptual (not exploited/verified against a live system, since this is a design-level analog inferred from code reading, not a demonstrated exploit):
1. Attacker holds valid auth for `workflowA` (owned by attacker).
2. Attacker learns or guesses the `requestID` the victim will use to trigger `workflowB` (owned by victim).
3. Attacker sends `HandleUserTriggerRequest` for `workflowA` with `req.ID = "victim-req-id"` just before the victim's real request arrives.
4. `setupCallback` inserts `h.callbacks["victim-req-id"]` for the attacker's own request.
5. Victim's legitimate request for `workflowB` with the same `req.ID` hits `setupCallback`, finds the ID already present, and is rejected via `jsonrpc.ErrConflict`, denying the victim's intended workflow execution until the attacker's entry times out. [6](#0-5)

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L53-61)
```go
type httpTriggerHandler struct {
	services.StateMachine
	config                  ServiceConfig
	shards                  []*shardEndpoint
	nodeAddrToShard         map[string]*shardEndpoint
	lggr                    logger.Logger
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
	stopCh                  services.StopChan
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-435)
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

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
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
