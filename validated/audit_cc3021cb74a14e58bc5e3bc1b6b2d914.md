### Title
HTTP Trigger request-ID griefing enables unprivileged DoS of another workflow's in-flight request - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
The gateway's HTTP Trigger handler tracks in-flight user requests in a single global map keyed only by the client-supplied `requestID` string, with no scoping to the requesting workflow, owner, or caller. Any authenticated caller of the HTTP Trigger endpoint (which only needs a valid workflow key/JWT for *some* registered workflow, not the victim's) can pre-occupy an arbitrary `requestID` value before a legitimate caller uses it, causing the legitimate request to be rejected with a "conflict" error. This is the same class of bug as the LpToken taint griefing report: a shared, insufficiently-scoped piece of state that any unprivileged party can write to is used to gate another user's legitimate operation, letting an attacker cheaply block it.

### Finding Description
`httpTriggerHandler.callbacks` is declared as `map[string]savedCallback // requestID -> savedCallback`, a single map shared across the entire gateway handler instance (i.e. across all workflows and all callers), not partitioned per-workflow or per-caller: [1](#0-0) 

The request flow is: validate → resolve workflow → `authorizeRequest` (JWT check tied to the *target workflow's* key, not to the specific caller's identity) → `checkRateLimit` → `setupCallback`, where the collision check happens: [2](#0-1) 

Because `requestID` is a plain, caller-supplied string (per the handler's own documentation, "User Requests: Plain string identifiers"), and the `callbacks` map is not namespaced by workflow ID or owner, any caller who can invoke the HTTP Trigger endpoint for *any* workflow can insert an entry under a `requestID` value that a victim (invoking a *different* workflow) is expected to use next. If the victim's SDK/client generates predictable, low-entropy, or commonly-reused IDs (e.g. sequential counters, fixed strings, or timestamp-based IDs — nothing in the validated format prevents this), the attacker's cheap pre-registration will cause the victim's genuine request to be rejected via `jsonrpc.ErrConflict` before it is ever dispatched to the DON: [3](#0-2) 

This mirrors the LpToken bug class precisely: a state slot (`_lastEvent[ubo]` / here `callbacks[requestID]`) that is writable by an unrelated third party is used to gate a legitimate user's action, and the legitimate actor has no way to "reserve" or scope the slot to themselves in advance.

### Impact Explanation
An unprivileged attacker (holding a valid JWT for any workflow they control, or exploiting workflows with no meaningfully-restricted trigger access) can selectively deny service to a specific victim's workflow-execution HTTP trigger request by squatting on the `requestID` they are expected to send. Because HTTP triggers are the mechanism used to kick off on-chain/off-chain workflow executions (e.g. fulfilling time-sensitive off-chain requests), this griefing can block or delay execution of a targeted workflow request, causing missed deadlines or dropped triggers, at negligible cost to the attacker (a single crafted request with a guessed/known ID).

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or observe the `requestID` the victim will use. Since the endpoint accepts free-form string IDs and imposes no requirement for high entropy or caller-binding, clients using simple/sequential/reused IDs (common in many integrations) are directly exploitable. Even without prediction, an attacker could pre-populate a wide set of likely ID values (numbers, UUID templates, timestamps) to increase collision odds cheaply, since the only cost is the attacker's own valid JWT for their own workflow and a rate-limit budget.

### Recommendation
Scope the in-flight `callbacks` map by a composite key that includes the caller-authenticated workflow identity (e.g. `(workflowID, requestID)` or `(workflowOwner, workflowID, requestID)`) rather than by `requestID` alone, so that a request-ID collision can only occur between two requests targeting the *same* workflow made by parties who are already authorized for that workflow. Additionally, consider requiring server-issued or high-entropy request identifiers, or binding `requestID` uniqueness to the caller's authenticated key rather than a global namespace.

### Proof of Concept
1. Attacker registers or controls workflow `A` and obtains a valid JWT for it.
2. Attacker observes/guesses that a victim's workflow `B` client will next send an HTTP trigger request with `requestID = "42"` (e.g., a sequential counter used by the victim's SDK).
3. Attacker sends a valid HTTP trigger request to workflow `A` using `ID: "42"`. `setupCallback` inserts `callbacks["42"]` into the shared map: [2](#0-1) 
4. Before the attacker's request completes/expires, the victim sends their legitimate request for workflow `B` with the same `ID: "42"`.
5. `setupCallback` finds `h.callbacks["42"]` already present and rejects the victim's request with `jsonrpc.ErrConflict` ("has already been used"), exactly as demonstrated by the existing test for same-workflow collisions: [4](#0-3) 
   The victim's workflow execution is denied, even though the victim was never involved with the attacker's workflow.

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
