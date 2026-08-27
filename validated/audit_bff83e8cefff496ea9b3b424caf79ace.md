The strongest reachable analog to the "liquidation blocks legitimate action" bug class in this codebase is a shared, un-namespaced in-flight-request lock in the CRE Gateway's HTTP trigger path, where a caller authorized for one workflow can collide with — and block — a caller of a completely different workflow simply by choosing the same client-supplied request ID.

### Title
Global, un-namespaced request-ID lock in HTTPTriggerHandler allows cross-workflow denial of service - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`httpTriggerHandler.setupCallback` guards against duplicate in-flight requests using a single map, `h.callbacks`, keyed only by the caller-supplied `requestID` string, with no workflow or owner scoping. Any caller who is authorized to trigger *some* workflow can occupy a `requestID` value before another, unrelated workflow's legitimate caller uses the same ID, causing the legitimate caller's trigger execution to be rejected.

### Finding Description
`HandleUserTriggerRequest` validates the request, resolves the workflow, and authorizes it against that specific workflow's key/JWT via `authorizeRequest` [1](#0-0) . It then calls `setupCallback`, which checks and reserves the requestID slot: [2](#0-1) 

The map `h.callbacks map[string]savedCallback` is declared at the handler (gateway-instance) level, not per-workflow or per-owner [3](#0-2) . Because the key is the raw, attacker/caller-chosen `requestID` string with no workflow-ID or owner prefix mixed in, any two independently-authorized callers targeting *different* workflows can collide on the same ID. Whoever's request reaches `setupCallback` first wins the slot; the second caller is rejected with `jsonrpc.ErrConflict` and the message "requestID: %s has already been used" [4](#0-3) , exactly mirroring the report's pattern of one actor's action (liquidation increment) blocking a different, unrelated actor's legitimate action (deposit) via a shared piece of state that isn't scoped to the actor it should protect.

The equivalent lookup for node responses, `HandleNodeTriggerResponse`, also indexes purely by `resp.ID` into the same global map [5](#0-4) , so a collision could also affect completion routing if node namespacing elsewhere in the system is imperfect.

### Impact Explanation
If request IDs used by real workflows are ever predictable, reused as idempotency keys shared with users, or simply guessable/small in entropy, an unprivileged (with respect to the victim's workflow) caller who merely has valid auth for their own workflow can grief a target workflow owner by pre-registering the same ID, causing the victim's legitimate `workflows.execute` trigger to fail with a conflict error until the slot is reaped (`CleanUpPeriodMs`) [6](#0-5) . This is a targeted denial-of-service against a specific workflow execution, potentially preventing time-sensitive workflow runs (analogous to preventing the victim from "repaying"/acting in time).

### Likelihood Explanation
Exploitation requires the attacker to know or guess the victim's chosen `requestID` and to have their own valid workflow authorization, and to win the race to submit before the victim. This is not a passive network-level or operator-only attack — it is reachable purely from the public gateway's `workflows.execute` API by any two independent, unprivileged clients, which matches the required "unprivileged-actor" scope, but real-world exploitability depends on request-ID predictability/guessability, which is not guaranteed in all deployments.

### Recommendation
Namespace the `h.callbacks` (and any node-response routing) key by `(workflowID or workflowOwner, requestID)` rather than by the raw client-supplied `requestID` alone, so that request-ID collisions across unrelated workflows/owners cannot occur.

### Proof of Concept
1. Attacker A holds valid gateway auth for `workflowID_A` (owned by Attacker A).
2. Victim B is about to invoke `workflowID_B` (owned by Victim B) with `requestID = "X"` (known/guessed by Attacker A, e.g., an application-level idempotency key, sequence number, or leaked value).
3. Attacker A submits a `workflows.execute` request for `workflowID_A` using `requestID = "X"` slightly before Victim B's request arrives; this passes `authorizeRequest` (valid for workflow A) and successfully calls `setupCallback`, inserting `h.callbacks["X"]` [7](#0-6) .
4. Victim B's request for `workflowID_B` with the same `requestID = "X"` reaches `setupCallback`, finds the slot occupied, and is rejected with `jsonrpc.ErrConflict`/"has already been used" [4](#0-3) , even though Victim B is fully authorized for their own workflow and had no relationship to Attacker A's workflow.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L94-106)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L448-455)
```go
func (h *httpTriggerHandler) HandleNodeTriggerResponse(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	h.lggr.Debugw("handling trigger response", "requestID", resp.ID, "nodeAddr", nodeAddr, "error", resp.Error, "result", resp.Result)
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()
	saved, exists := h.callbacks[resp.ID]
	if !exists {
		return errors.New("callback not found for request ID: " + resp.ID)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L526-544)
```go
// reapExpiredCallbacks removes callbacks that are older than the maximum age
func (h *httpTriggerHandler) reapExpiredCallbacks(ctx context.Context) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()
	now := time.Now()
	var expiredCount int
	for reqID, callback := range h.callbacks {
		if now.Sub(callback.createdAt) > time.Duration(h.config.CleanUpPeriodMs)*time.Millisecond {
			h.metrics.IncrementRequestErrors(ctx, jsonrpc.ErrInternal, h.lggr)
			h.cleanupCallback(reqID)
			expiredCount++
		}
	}
	if expiredCount > 0 {
		h.metrics.IncrementPendingRequestsCleanUpCount(ctx, int64(expiredCount), h.lggr)
		h.lggr.Infow("Removed expired callbacks", "count", expiredCount, "remaining", len(h.callbacks))
	}
	h.metrics.RecordPendingRequestsCount(ctx, int64(len(h.callbacks)), h.lggr)
}
```
