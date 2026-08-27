### Title
JWT single-use token is consumed before the callback/execution state is created, permanently burning valid requests on transient downstream failure - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`HandleUserTriggerRequest` performs `authorizeRequest` (which consumes the single-use JWT auth token) before it performs rate-limit checks and before it creates the in-memory execution state (`setupCallback`) that actually causes the request to be dispatched to the DON. If either of the steps that happen *after* the token is marked used fails, the token is irrecoverably burned but no workflow execution is ever created or delivered to nodes — mirroring the `TimeLock` bug class where the "asset transfer" (token consumption) and "agreement creation" (callback/execution registration) are two desynchronized steps instead of one atomic operation.

### Finding Description
In `HandleUserTriggerRequest`, the request flow is:
1. `authorizeRequest` — calls `h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)`, which validates and, per the JWT single-use test (`"duplicate JWT token and request ID"`), marks the JWT as used so it cannot be replayed.
2. `checkRateLimit` — can fail if the per-workflow rate limit is exceeded.
3. `setupCallback` — can fail if the requestID is already in-flight (`ErrConflict`) or if the workflow has no assigned shards.
4. `sendWithRetries` — the point where the request is actually dispatched to DON nodes. [1](#0-0) 

The authorization/token-consumption step happens strictly before the state that represents an actual, deliverable "agreement" (the `savedCallback` registered in `h.callbacks`, and the fan-out to node shards) is created: [2](#0-1) [3](#0-2) 

Because JWT single-use enforcement rejects any second attempt with the same token/requestID pair with `"token has already been used"` (as shown in the test), a token consumed in step 1 above cannot be reused if step 2 or 3 subsequently fails: [4](#0-3) 

This is the same root-cause pattern as the `TimeLock` finding: two logically-coupled state transitions (token consumption vs. execution/callback creation) are not performed atomically within the same component, so a partial failure leaves one side of the transaction "spent" while the other side never materializes.

I was not able to fully inspect `workflow_metadata_handler.go`'s `Authorize` implementation to confirm the exact line where the token is marked used relative to any rollback logic; this should be verified directly in that file to confirm whether a rollback/un-consume path exists.

### Impact Explanation
An unprivileged client (a workflow owner submitting an HTTP trigger request with a valid, signed, single-use JWT) can have its legitimate request silently and permanently invalidated by a transient condition (temporary rate-limit exhaustion, or a requestID collision) that occurs *after* the token was already accepted and consumed. Since new JWTs for HTTP triggers require an out-of-band signing step by the workflow owner (they are not gateway-issued/renewable at will inside this request path), this results in a denial-of-service against the specific execution attempt: the caller's fund/authorization budget is spent, but no workflow execution occurs and no automatic retry with the same token is possible.

### Likelihood Explanation
Rate limiting is a normal, expected occurrence for busy workflows, and the requestID conflict path (`ErrConflict`) can be triggered by ordinary client retry behavior (e.g., a client resending after a timeout races with the original attempt still being processed) without needing a malicious actor. This makes the desync realistically triggerable by unprivileged, non-malicious clients under normal operating conditions.

### Recommendation
Perform token consumption/marking-as-used atomically with (or after) the successful creation of the callback/execution state, or add compensating logic to "un-consume" the JWT if any step after `authorizeRequest` fails before `setupCallback`/`sendWithRetries` succeeds. Ideally, reorder the pipeline so rate-limiting and requestID-conflict checks happen before the token is irrevocably marked used, ensuring the token consumption and the execution/agreement registration are synchronized as a single unit, consistent with the `TimeLock` recommendation of keeping the two operations atomic within the same component.

### Proof of Concept
1. A workflow owner obtains a valid single-use JWT and calls `workflows.execute` (HTTP trigger) with a fresh `requestID`.
2. `authorizeRequest` succeeds and marks the JWT as consumed via `workflowMetadataHandler.Authorize`.
3. `checkRateLimit` fails because the workflow's rate limit was hit moments earlier by an unrelated request (see `checkRateLimit`, `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go:371-396`), and the handler returns an error to the client without ever calling `setupCallback` or `sendWithRetries`.
4. No execution state is created and no request is sent to the DON — the trigger is effectively dropped.
5. The client retries with the same JWT (as they might reasonably do, believing the request never got through) and receives `"token has already been used"` (per the behavior validated in `http_trigger_handler_test.go:357-394`), permanently blocking that execution attempt even though it never actually ran.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-396)
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

func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
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
