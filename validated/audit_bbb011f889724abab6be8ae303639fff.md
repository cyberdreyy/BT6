### Title
Request-ID reuse across different workflows/owners allows cross-execution response confusion in `cleanupCallback`/`HandleNodeTriggerResponse` - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`h.callbacks` is keyed only by the client-supplied `req.ID` string, with no binding to the workflow ID/owner or to a specific execution generation. Once a request completes and `cleanupCallback` removes the entry, the same ID string can be immediately reused by a different (unrelated) trigger request; a late-arriving node response belonging to the previous execution can then be routed into the new callback's `IdenticalNodeResponseAggregator`, mixing an old execution's data into a new, unrelated request.

### Finding Description
`HandleUserTriggerRequest` accepts an attacker/client-controlled `req.ID` (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go:88-140`) and only checks for *currently in-flight* collisions in `setupCallback` (line 402-405): `if _, found := h.callbacks[requestID]; found { ... conflict ... }`. There is no per-workflow or per-owner namespacing of the `callbacks` map key — it is a single global map shared by every workflow/owner using the gateway [1](#0-0) .

`cleanupCallback` deletes the map entry as soon as the first shard reaches quorum and a response is sent (`HandleNodeTriggerResponse`, line 481-495), which also holds `callbacksMu` for the *entire* duration of the lookup, aggregation, send, and cleanup, so there is no race window inside a single call [2](#0-1) . However, after cleanup completes, the ID is free to be reused instantly by *any* other trigger request (`setupCallback` only checks presence, not history) [3](#0-2) .

`HandleNodeTriggerResponse` routes a node's response purely by `resp.ID` → `saved.responseAggregators[shard.donID]`, with no check that the response actually corresponds to the workflow/execution that originally created the callback for that ID [4](#0-3) . If a slow/losing-shard node's response for execution A arrives after (a) A's callback was cleaned up and (b) a different execution B (potentially a different workflow/owner) has re-registered the same `req.ID` and is assigned to a shard containing that same node, the aggregator built in `IdenticalNodeResponseAggregator.CollectAndAggregate` (`core/services/gateway/common/aggregation/response_aggregator.go:38-75`) will accept and count that stale response toward B's quorum, since the aggregator only keys on response digest and node address, with no execution/workflow binding [5](#0-4) .

### Impact Explanation
This is a cross-execution/cross-workflow response-confusion bug: stale node output belonging to workflow A's execution can be attributed to workflow B's in-flight aggregator, potentially contributing votes toward — or ultimately delivering — data from an unrelated execution to a different requester. In the worst case (small shard threshold, or an entire shard being uniformly slow so that several members' late responses arrive together after reuse), this could let an attacker who controls the timing/ID of their own request receive fragments of a previous, unrelated execution's aggregated result. This matches the "cross-user response confusion" impact class.

### Likelihood Explanation
Exploitability is timing- and ID-dependent, not guaranteed on-demand: the attacker needs (1) knowledge or prediction of a `req.ID` string used by a victim (or targets their own recently-completed ID against another workflow they also control), (2) the victim's shard(s) to overlap with the attacker's newly-registered workflow's assigned shard(s) (shared node membership), and (3) the victim's losing shard to still be in-flight (delayed) at the moment the attacker's new callback is registered under the same ID. No special privilege is needed beyond being a normal gateway client able to submit trigger requests with self-chosen IDs, but the race window is narrow and probabilistic, making this a moderate-likelihood, not readily on-demand, issue.

### Recommendation
Scope the callback map key by workflow ID (and ideally owner) in addition to `req.ID`, e.g. `workflowID + "/" + requestID`, so that late responses for one workflow's execution can never be routed into another workflow's aggregator. Additionally, consider embedding a per-registration execution nonce/generation counter in `savedCallback` and validating it against an execution-scoped identifier carried in the node response, rejecting any response whose generation does not match the currently registered one.

### Proof of Concept
Go unit test outline for `http_trigger_handler_test.go`:
1. Register callback for `requestID = "X"`, workflow A, with shard S having 2 aggregators (threshold=1 for simplicity).
2. Deliver a response from shard S's node N1 for `X`; quorum reached, `SendResponse` called, `cleanupCallback("X")` removes the entry.
3. Immediately call `setupCallback` again for `requestID = "X"`, this time for workflow B (different owner), also assigned to shard S (same node N1).
4. Deliver a late response from N1 that was actually generated for workflow A's execution (same node address, same requestID "X", but stale payload).
5. Assert: `HandleNodeTriggerResponse` accepts it into workflow B's aggregator (`saved.responseAggregators[S.donID]`) and, if threshold is met, `SendResponse` is invoked for workflow B's callback carrying workflow A's stale payload — demonstrating cross-workflow data leakage rather than a clean "callback not found" rejection.

### Citations

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

**File:** core/services/gateway/common/aggregation/response_aggregator.go (L38-75)
```go
func (agg *IdenticalNodeResponseAggregator) CollectAndAggregate(
	resp *jsonrpc.Response[json.RawMessage],
	nodeAddress string) (*jsonrpc.Response[json.RawMessage], error) {
	if resp == nil {
		return nil, errors.New("response cannot be nil")
	}
	if nodeAddress == "" {
		return nil, errors.New("node address cannot be empty")
	}

	key, err := resp.Digest()
	if err != nil {
		return nil, fmt.Errorf("error generating digest for response: %w", err)
	}

	// Check if the node already submitted a different response
	if oldKey, exists := agg.nodeToResponse[nodeAddress]; exists && oldKey != key {
		if nodes, ok := agg.responses[oldKey]; ok {
			nodes.Remove(nodeAddress)
			// Clean up empty response groups
			if len(nodes) == 0 {
				delete(agg.responses, oldKey)
			}
		}
	}

	if _, ok := agg.responses[key]; !ok {
		agg.responses[key] = make(StringSet)
	}
	agg.responses[key].Add(nodeAddress)
	agg.nodeToResponse[nodeAddress] = key

	if len(agg.responses[key]) >= agg.threshold {
		return resp, nil
	}

	return nil, nil
}
```
