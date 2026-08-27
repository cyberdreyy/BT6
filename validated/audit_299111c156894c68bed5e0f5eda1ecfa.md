### Title
Cross-user request ID collision in gateway HTTP Trigger handler enables denial-of-service against another workflow's in-flight request - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The reported bug class is: an unprivileged actor supplies an identifier that is meant to scope/attribute an action (liquidity `recipient`) but the identifier is not bound to the caller's own identity, so an attacker can use it to interfere with another user's state (locking them in cooldown). The closest verified analog in this Chainlink repository is in the gateway's HTTP Trigger handler (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`): the request-tracking key (`req.ID`, the JSON-RPC request ID) is fully client-chosen and is used as the **sole, globally-scoped** key into the shared `callbacks` map, without being bound to the authenticated workflow/signer. Two different, independently-authenticated callers (each authorized only for their own workflow) can supply the identical `req.ID`, and whichever request reaches `setupCallback` first wins the map slot, causing the other caller's in-flight request to be rejected with a `Conflict` error.

### Finding Description
`HandleUserTriggerRequest` processes each inbound trigger request in this order: `validatedTriggerRequest` → `resolveWorkflowID` → `authorizeRequest` (JWT signature verification, scoped per-`workflowID`) → `checkRateLimit` → `setupCallback`. [1](#0-0) 

Crucially, `req.ID` is a user-supplied string that is only validated for non-emptiness and absence of `/` — it is never required to be globally unique across workflows/owners, nor is it derived from or bound to the authenticated signer: [2](#0-1) 

`setupCallback` then uses this attacker-controllable `req.ID` as the key into a single, DON-wide `callbacks` map shared by all workflows/owners: [3](#0-2) 

The map itself is declared without any owner/workflow-scoped composite key — it is `map[string]savedCallback // requestID -> savedCallback`: [4](#0-3) 

Because authorization (`authorizeRequest`) succeeds independently for each caller's *own* workflow — a caller only needs a valid JWT signed by a key authorized for the workflow *they* specify, not for the workflow tied to the `req.ID` string — an attacker (Workflow B's legitimate, authorized caller) can deliberately or opportunistically choose the same `req.ID` value that a victim (Workflow A's caller) is concurrently using. The second call to reach `setupCallback` observes `req.ID` already present in the map and is rejected: [3](#0-2) 

This is functionally analogous to the QuickSwap bug: an identifier that should scope an operation to "self" (there, `recipient` in `mint`; here, `req.ID` as the callback/response routing key) is instead attacker-supplied and globally shared, letting one unprivileged party's legitimate action interfere with another unprivileged party's pending operation.

### Impact Explanation
If an attacker can predict or observe a victim's `req.ID` (e.g., IDs following a predictable scheme, or an attacker racing to reuse a very common/short ID string before the victim's request lands), the attacker can:
- Cause the victim's legitimate trigger request to fail with `jsonrpc.ErrConflict` ("requestID: %s has already been used"), denying service to that specific workflow execution attempt.
- Repeat this cheaply and continuously against a targeted `req.ID` pattern to keep a specific workflow trigger from ever being accepted, a low-cost, repeatable DoS primitive against a single victim's request, without needing any privileges beyond having their own valid (unrelated) workflow and signer key.

This does not cause direct fund loss or key/secret disclosure, so it is lower severity than the original High-severity liquidity-lock finding, but it fits the "unauthorized job run / cross-user response confusion" category the task calls out, since it lets one unprivileged caller consume a resource (the callback slot) that was meant to be exclusively usable per-request by the intended caller.

### Likelihood Explanation
Likelihood is **low-to-moderate**: `req.ID` values are typically UUIDs chosen by legitimate workflow callers/tooling, making blind collision unlikely. However, the check performs no ownership binding at all, so any scenario where `req.ID` values are non-random, sequential, or otherwise predictable/observable (e.g., logs, shared client libraries, deterministic execution IDs derived from workflow+trigger metadata) would make this trivially exploitable by any unprivileged, authenticated caller with their own valid workflow — no elevated privileges required.

### Recommendation
Scope the `callbacks` map key by `(workflowID, req.ID)` (or another value bound to the authenticated identity/workflow resolved in `authorizeRequest`) rather than by the raw client-supplied `req.ID` alone, so that request-ID collisions across different workflows/owners cannot interfere with each other. Perform this scoping/lookup only after authorization has succeeded and workflow identity is established, mirroring the practice used elsewhere in the code for per-workflow rate limiting via `contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})`. [5](#0-4) 

### Proof of Concept
1. Attacker registers/owns Workflow B with a valid authorized signer key (obtained normally, no special privilege).
2. Victim submits a `workflows.execute` trigger request for Workflow A with `req.ID = "X"`, signed with a JWT authorized for Workflow A. This request begins processing and is in-flight (has not yet completed `setupCallback`/received a quorum response).
3. Attacker submits their own `workflows.execute` trigger request for Workflow B, also with `req.ID = "X"`, signed with their own JWT authorized for Workflow B.
4. Whichever request reaches `setupCallback` second observes the collision at: [6](#0-5) 
and is rejected with `jsonrpc.ErrConflict`, denying that party's request — this is confirmed to be reachable and tested (for the single-caller retry case) in: [7](#0-6) 
The existing test only exercises the same-caller-retries-with-same-ID case; it does not test (and the code does not prevent) two *different, unrelated, independently-authorized* workflows colliding on the same `req.ID`, which is the cross-user DoS scenario described above.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L53-66)
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
	workflowMetadataHandler *WorkflowMetadataHandler
	userRateLimiter         limits.RateLimiter
	metrics                 *metrics.Metrics
	wg                      sync.WaitGroup
}
```

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L371-396)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-406)
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
