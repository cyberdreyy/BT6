### Title
Cross-user response confusion via requestID reuse in `httpTriggerHandler` callback map - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`h.callbacks` is keyed only by the client-supplied JSON-RPC `req.ID` string, with no binding to workflowID or caller session, and `HandleNodeTriggerResponse` matches an incoming node response to a callback purely by that same string plus a shard-ID lookup that is unrelated to the specific in-flight execution. Because `setupCallback` allows the same `requestID` to be reused as soon as the previous entry is removed (via `cleanupCallback`, either on quorum success or reaping), a late/straggling node response belonging to a first caller's already-cleaned-up request can be aggregated into and delivered to a second, unrelated caller's callback if both workflows share a DON shard.

### Finding Description
`setupCallback` at [1](#0-0)  stores callbacks in a global map `h.callbacks map[string]savedCallback // requestID -> savedCallback` [2](#0-1) , keyed solely by the attacker/user-controlled `requestID` string. If an entry already exists for that ID, the second caller is correctly rejected with `jsonrpc.ErrConflict` — so a true "in-flight" collision cannot silently overwrite [3](#0-2) . However, once the first entry is removed by `cleanupCallback` (called either after a quorum is reached in `HandleNodeTriggerResponse` [4](#0-3)  or by the periodic `reapExpiredCallbacks` [5](#0-4) ), a second, completely unrelated caller (different workflow, different signer) can immediately register a new `savedCallback` under the exact same `requestID` string.

`HandleNodeTriggerResponse` then looks up the callback purely by `resp.ID` and routes the response using only the node's shard membership (`h.nodeAddrToShard[nodeAddr]`) against `saved.responseAggregators[shard.donID]` — it never verifies that the responding node was actually answering *this* caller's execution (there is no workflowID/executionID binding inside the aggregation key or response validation) [6](#0-5) . `IdenticalNodeResponseAggregator.CollectAndAggregate` aggregates purely by response content digest and node address, with no workflow/session context [7](#0-6) .

Consequently, if node(s) belonging to caller1's DON shard send a late response for the already-cleaned-up request (e.g., in-flight network responses that hadn't been processed when `cleanupCallback` ran, or nodes that are slow to reply), and caller2 has since registered the identical `requestID` for a workflow that is assigned to an overlapping shard, that stale response is accepted as legitimate input to caller2's aggregator. If enough identical stale responses accumulate to reach caller2's aggregation threshold, `saved.SendResponse` delivers caller1's execution result to caller2 — a cross-user result leak — without any error surfaced to either party.

### Impact Explanation
This is a cross-user isolation violation: an unprivileged, authorized caller of one workflow can potentially receive execution output belonging to another unrelated workflow's caller, because response routing keys are not scoped per-session/per-workflow. This matches the "cross-user response confusion / result leak to wrong caller" impact class — sensitive workflow execution output (which may include business logic results, off-chain data, or transaction payloads) intended for one tenant could be exposed to another.

### Likelihood Explanation
Exploitation requires: (1) the attacker controls two authorized callers (or predicts/collides with another tenant using low-entropy, common request IDs such as "1", "abc", timestamps, etc., since request IDs are entirely user-chosen and uniqueness is only enforced while the first entry is live); (2) both workflows must be assigned to at least one common DON shard; (3) a timing race between `cleanupCallback` of the first request and re-registration under the same ID by the second, combined with a straggling node response from the first execution. This is a narrow, timing-dependent race rather than a deterministic exploit, and the attacker has no way to force the race precisely — but it is repeatable in principle by flooding predictable IDs and relying on natural request/response jitter, especially in high-throughput multi-tenant gateway deployments where common shards are shared across many workflows.

### Recommendation
Bind callback identity to more than the bare client-supplied `requestID`: key `h.callbacks` (and the aggregator lookup in `HandleNodeTriggerResponse`) by a composite key that includes `workflowID` (or the full `executionIDWithTriggerIndex`) plus `requestID`, and additionally validate that the responding node's shard is the shard(s) assigned to *that specific* execution/workflowID at the time the request was sent (not just currently assigned in `nodeAddrToShard`). Reject any node response whose shard/workflow binding cannot be verified rather than silently aggregating it.

### Proof of Concept
Go unit test plan targeting `http_trigger_handler_test.go`:
1. Configure two workflows, A and B, both assigned to the same shard `donID` with overlapping node membership.
2. Call `HandleUserTriggerRequest` for workflow A with `req.ID = "X"` and a fake `callbackA` capturing `SendResponse` calls; do not yet deliver enough node responses to reach quorum.
3. Manually invoke `HandleNodeTriggerResponse` for `threshold-1` nodes of the shard with `resp.ID = "X"` and a distinctive payload `resultA`, leaving quorum unmet.
4. Simulate cleanup: directly call `h.cleanupCallback("X")` to emulate a reap/successful completion out from under the pending node responses (representing the race window).
5. Call `HandleUserTriggerRequest` for workflow B, reusing `req.ID = "X"`, with `callbackB`; confirm `setupCallback` succeeds (no conflict) because the map entry was removed.
6. Deliver the remaining `threshold` node responses with `resp.ID = "X"` and payload `resultA` (simulating stale/late node replies originally intended for A).
7. Assert whether `callbackB.SendResponse` is invoked with `resultA` — if so, this proves cross-user delivery of A's data into B's callback, confirming the isolation violation described above.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L59-60)
```go
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L448-487)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L489-491)
```go
	// First shard to reach quorum wins: only after successfully sending the
	// response, clean up the callback (closes doneCh, stopping all shard sends).
	h.cleanupCallback(resp.ID)
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
