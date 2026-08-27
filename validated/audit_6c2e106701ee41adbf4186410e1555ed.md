### Title
Global, non-namespaced `requestID` keyspace in `httpTriggerHandler` allows cross-user response hijack after `reapExpiredCallbacks` frees an in-flight ID - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`h.callbacks` is keyed only by the client-supplied `requestID` string, globally across all workflows/users, with uniqueness enforced only while an entry is live (`setupCallback` returns `ErrConflict` for a duplicate ID). `reapExpiredCallbacks` deletes an entry purely based on that entry's own `createdAt` versus the fixed `CleanUpPeriodMs`, and once deleted, that ID is free for anyone to reclaim, including an attacker who deliberately reuses a request ID that a slow-to-respond victim request is still using in flight upstream. If a genuine (honest) node responds after the reap but with the same wire ID, `HandleNodeTriggerResponse` will route that response into whatever `savedCallback` currently occupies that ID slot in the map — which can now be the attacker's own request.

### Finding Description
- `validateRequestID` only checks the ID is non-empty and does not contain `/`; it is fully attacker-chosen and not scoped/namespaced per workflow or per caller: [1](#0-0) 
- `setupCallback` stores the callback in a single global map `h.callbacks map[string]savedCallback` keyed only by `requestID`, and only rejects a *duplicate, still-live* ID: [2](#0-1) 
- `reapExpiredCallbacks` evaluates each entry independently against its own `createdAt` and the operator-fixed `CleanUpPeriodMs`; once an entry is reaped, `cleanupCallback` closes `doneCh` (stopping the victim's local retry loop) and deletes the map entry, freeing the ID for reuse by anyone: [3](#0-2) [4](#0-3) 
- Reaping the victim's local map entry does **not** cancel or invalidate the request already forwarded to the node; an honest node can still reply later using the same wire request ID.
- `HandleNodeTriggerResponse` looks up the incoming node response purely by `resp.ID` in the same global map and routes it to whichever `savedCallback` currently owns that key, based only on shard/node membership — it performs no check that the response belongs to the same workflow/session that originally created the entry: [5](#0-4) 

Exploit flow: if an attacker can predict/observe the victim's chosen `requestID` (IDs are plain user-supplied strings, not random/opaque), and the victim's node response is delayed past `CleanUpPeriodMs` (natural network slowness, not attacker-controlled), the attacker can submit their own `workflows.execute` request using the identical `requestID` once it is freed by the reaper. If the victim's shard(s) are the same DON shard(s) the attacker's workflow is assigned to, the eventually-arriving, delayed node response for the victim's original request will be captured by the attacker's freshly-registered `savedCallback` and returned to the attacker via `SendResponse`, violating the "response reaches only its own requester" invariant.

Note on the other two prongs of the question: flooding `h.callbacks` with many entries does **not** accelerate reaping of a specific victim entry, since each entry's expiry is computed independently from its own `createdAt` (`now.Sub(callback.createdAt) > CleanUpPeriodMs`) — this vector is not exploitable. `requestStartTime`/`createdAt` are always set server-side to `time.Now()` at submission (`setupCallback`), not attacker-influenceable directly — this vector is also not exploitable on its own. The exploitable component is purely ID collision/reuse enabled by the reaper freeing IDs without any mechanism to prevent stale in-flight node responses from binding to a newly-registered, unrelated callback.

### Impact Explanation
Successful exploitation causes cross-user response confusion: the attacker's callback can receive another user's workflow execution result, which may include private computation output/data intended solely for the victim's caller. This matches the "unauthorized action on another user's job" / "request impersonation" bounty class, since the invariant "responses must reach only their own requester" is violated. The victim also silently loses its response (since the map entry the honest node's response would have targeted no longer belongs to the victim).

### Likelihood Explanation
Exploitation requires several conditions to align, making this an opportunistic, not deterministic, exploit:
1. The attacker must know/guess the exact `requestID` string chosen by the victim (no cryptographic randomness is enforced on IDs by the code — it's whatever the calling client picks) — feasible if callers use predictable/sequential/reused IDs, but not guaranteed in general.
2. The attacker must have a registered/authorized workflow of their own (they cannot forge auth for the victim's workflow — `authorizeRequest`/`Authorize` still gates request submission) whose shard assignment overlaps with the victim's shard(s), and must win the race to re-register the ID after reap but before the stale node reply arrives.
3. The delay causing the victim's request to be reaped (a node response taking longer than `CleanUpPeriodMs`, default 10 minutes) is not attacker-controlled; it depends on normal node/network latency, which the attacker cannot reliably induce without violating the "no malicious node/network-layer" exclusion.

Given these preconditions, likelihood is low-to-moderate and situational rather than trivially repeatable on demand, but the underlying design flaw (global unscoped ID keyspace with no rebinding protection after reap) is real and independently confirmable via unit test.

### Recommendation
- Namespace `h.callbacks` keys by a server-generated identifier or a composite key that includes workflow ID/owner, not solely the client-supplied `requestID`, so cross-user/cross-workflow collisions are structurally impossible.
- When reaping (or when a new `setupCallback` reuses an ID), explicitly invalidate/fence off any late node responses tied to the previous "generation" of that request ID (e.g., generation counter embedded in the routing key, or a short quarantine period during which reused IDs still route late responses to nowhere rather than to the new owner).
- Consider making `HandleNodeTriggerResponse` validate that the responding node's shard/workflow membership matches the workflow ID recorded when the corresponding callback was created, not just current map occupancy.

### Proof of Concept
Go handler-level integration test plan (extending `TestHttpTriggerHandler_ReapExpiredCallbacks` and `TestHttpTriggerHandler_MultiShardQuorumRace` patterns in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go`):
1. Configure `ServiceConfig{CleanUpPeriodMs: 100, MaxTriggerRequestDurationMs: 50}` as in the existing reap test.
2. Register two distinct workflows (`victimWorkflowID`, `attackerWorkflowID`) assigned to the same shard set via `registerWorkflowOnShards`.
3. Victim: call `handler.HandleUserTriggerRequest` with `req.ID = "shared-id"` for `victimWorkflowID`; capture `victimCallback`.
4. Manually backdate `handler.callbacks["shared-id"].createdAt` beyond `CleanUpPeriodMs` (as in the existing reap test) and call `handler.reapExpiredCallbacks(ctx)`; assert the entry is removed.
5. Attacker: call `handler.HandleUserTriggerRequest` with the same `req.ID = "shared-id"` for `attackerWorkflowID`; capture `attackerCallback`. This should succeed (no `ErrConflict`) since the ID was freed.
6. Simulate the victim's originally-dispatched (but delayed) node response arriving late: call `handler.HandleNodeTriggerResponse(ctx, nodeResp{ID: "shared-id", Result: victimPayload}, "node-in-shared-shard")` for each node until quorum.
7. Assert that `attackerCallback.Wait(ctx)` receives `victimPayload` (demonstrating cross-user delivery), and that `victimCallback` never receives a response (demonstrating the victim's response was hijacked/lost) — confirming the "request binding" invariant is broken.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L437-446)
```go
// cleanupCallback removes a callback and signals sendWithRetries to stop.
// Must be called while holding callbacksMu lock.
func (h *httpTriggerHandler) cleanupCallback(requestID string) {
	saved, exists := h.callbacks[requestID]
	if !exists {
		return
	}
	close(saved.doneCh)
	delete(h.callbacks, requestID)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L448-496)
```go
func (h *httpTriggerHandler) HandleNodeTriggerResponse(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	h.lggr.Debugw("handling trigger response", "requestID", resp.ID, "nodeAddr", nodeAddr, "error", resp.Error, "result", resp.Result)
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()
	saved, exists := h.callbacks[resp.ID]
	if !exists {
		return errors.New("callback not found for request ID: " + resp.ID)
	}

	// Route the response into the aggregator for the shard that owns this node.
	shard, ok := h.nodeAddrToShard[nodeAddr]
	if !ok {
		return fmt.Errorf("received trigger response from unknown node %s (no owning shard)", nodeAddr)
	}
	agg, ok := saved.responseAggregators[shard.donID]
	if !ok {
		// The node belongs to a shard this workflow isn't assigned to (or the
		// callback was captured before the workflow was assigned there).
		return fmt.Errorf("node %s (shard %s) is not assigned to workflow for request ID %s", nodeAddr, shard.donID, resp.ID)
	}
	aggResp, err := agg.CollectAndAggregate(resp, nodeAddr)
	if err != nil {
		return err
	}
	if aggResp == nil {
		h.lggr.Debugw("Not enough responses to aggregate", "requestID", resp.ID, "nodeAddress", nodeAddr, "shard", shard.donID)
		return nil
	}
	rawResp, err := json.Marshal(aggResp)
	if err != nil {
		return errors.New("failed to marshal response: " + err.Error())
	}

	err = saved.SendResponse(handlers.UserCallbackPayload{
		RawResponse: rawResp,
		ErrorCode:   api.NoError,
	})
	if err != nil {
		return err
	}

	// First shard to reach quorum wins: only after successfully sending the
	// response, clean up the callback (closes doneCh, stopping all shard sends).
	h.cleanupCallback(resp.ID)
	latencyMs := time.Since(saved.requestStartTime).Milliseconds()
	h.metrics.RecordRequestHandlerLatency(ctx, latencyMs, h.lggr)
	h.metrics.IncrementRequestSuccess(ctx, h.lggr)
	return nil
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
