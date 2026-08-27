### Title
Request ID reuse in `httpTriggerHandler` allows a stale node's late trigger response to be misdelivered to an unrelated, later request sharing the same `requestID` - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`httpTriggerHandler.callbacks` is a single global map keyed purely by the client-supplied JSON-RPC `requestID`, with no per-request generation/nonce to distinguish successive, unrelated uses of the same ID string. Once a request's callback is deleted via `cleanupCallback` (on quorum success or reaping), the ID becomes immediately reusable via `setupCallback`, and any node response that was already in flight for the old request but arrives after the new callback is registered will be routed by `HandleNodeTriggerResponse` into the new callback's aggregator, because the routing logic only checks `h.callbacks[resp.ID]` and the shard membership, not any identifier tying the response back to the specific request generation that produced it.

### Finding Description
`setupCallback` and `HandleNodeTriggerResponse` both serialize on `h.callbacksMu`, so a new callback can never be inserted for a `requestID` while an entry for that ID still exists in the map — `setupCallback` returns `jsonrpc.ErrConflict` in that case: [1](#0-0) 

However, once the first request reaches quorum, `HandleNodeTriggerResponse` calls `cleanupCallback`, which closes `doneCh` and deletes the map entry, immediately freeing the `requestID` for reuse — while the `sendToShard` retry loops for *other, non-winning* shards may still have in-flight `SendToNode` calls to nodes that haven't been told to stop: [2](#0-1) [3](#0-2) 

Once the ID is free, a subsequent `HandleUserTriggerRequest` call (for a *different* workflow/request) can call `setupCallback` and insert a brand-new `savedCallback` — with fresh `responseAggregators` — under the exact same map key: [4](#0-3) 

If a node that was contacted for the *old* request (still holding the old `requestID` in its own pending work) later sends its trigger response, `HandleNodeTriggerResponse` looks the response up purely by `resp.ID` and by whether the sending node's shard is present in `saved.responseAggregators` — there is no check that this response actually corresponds to the generation of the request that is currently registered under that ID: [5](#0-4) 

This is a genuine gap: existing tests only verify that a *stale response after cleanup and before reuse* is rejected with "callback not found" (`TestHttpTriggerHandler_MultiShardQuorumRace`), not the case where the ID has already been reused by a new, unrelated request by the time the stale response arrives: [6](#0-5) 

Note that `requestID`/`req.ID` is not otherwise scoped per-workflow or per-owner in the callback map key, and while `req.Auth` (a JWT) is signed over the request by the workflow's authorized key, the map itself has no field disambiguating two different `setupCallback` calls that reuse the same string ID at different times.

### Impact Explanation
If exploitable, this results in cross-request response confusion: a node's response intended for request generation A (already completed and cleaned up) is aggregated into and potentially delivered as the result of a completely unrelated, later request B that happens to reuse the same `requestID` string. This maps to Chainlink's "cross-user response confusion" impact class — an attacker's newly triggered workflow could receive output data/aggregation state belonging to a different (stranger's) prior execution, or conversely have their own execution result silently discarded/overwritten by a stray delayed response from an unrelated execution.

### Likelihood Explanation
The likelihood is low-to-moderate and highly timing-dependent, not attacker-deterministic:
- It requires two different requests (attacker's and a victim's, or the attacker's own two back-to-back requests) to select the identical `requestID` string. The attacker can only detect an ID collision via the `ErrConflict` response, and only if the colliding party is currently in-flight when they probe.
- It further requires a race window: the old request's callback must be cleaned up (quorum reached on a different shard, or reaping) while at least one still-pending `SendToNode`/node processing is outstanding for that old `requestID`, and a new `setupCallback` for a different request with the same ID must land before that stray node response arrives.
- Since `requestID` is not namespaced per-workflow/owner in the map, this is more of a systemic design gap (no generation/version isolation) than a directly attacker-forced exploit; reliably targeting a specific victim's ID is impractical unless request IDs are predictable/low-entropy (e.g., small sequential integers commonly reused by many independent clients), which increases feasibility somewhat but is not guaranteed in general.

### Recommendation
Add a monotonically increasing generation token (or a randomly generated internal correlation ID) to `savedCallback`, and have `HandleNodeTriggerResponse` validate that the response's originating request generation matches the one currently stored for that `requestID` before aggregating (e.g., embed the generation in the request sent to nodes and require it to be echoed back, or simply reject/ignore any node response whose implicit generation doesn't match the currently active entry). Alternatively, key `h.callbacks` by a server-generated unique correlation ID rather than the client-supplied `requestID` directly, and only use `requestID` for the client-facing JSON-RPC `id` field.

### Proof of Concept
Go integration test plan in `http_trigger_handler_test.go`:
1. Configure a 2-shard DON via `createMultiShardTriggerHandler` (as in `TestHttpTriggerHandler_MultiShardQuorumRace`), each shard 3 nodes, threshold 3.
2. Trigger request A with `requestID = "reuse-id"`; let shard 1 reach quorum (3 responses) so `HandleNodeTriggerResponse` calls `cleanupCallback("reuse-id")`, deleting the map entry — but withhold sending shard 0's node responses yet (simulate them as delayed/in-flight).
3. Immediately call `HandleUserTriggerRequest` again with a *different* workflow (registered on shard 0) but the *same* `requestID = "reuse-id"`, using a second callback B; `setupCallback` succeeds because the map entry was already deleted, creating a new `savedCallback` with a fresh `responseAggregators` keyed by shard 0's donID.
4. Deliver the withheld shard-0 node responses (from step 2, using the original request A's data/result) via `handler.HandleNodeTriggerResponse` with `resp.ID = "reuse-id"`.
5. Assert that `callback B.Wait()` receives the aggregated response — demonstrating that stale request A's node responses were delivered into request B's callback instead of being rejected — and that `callback A` had already completed independently via shard 1, showing the data crossed request-generation boundaries under the same `requestID`.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L426-434)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L448-468)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L708-741)
```go
		if len(successfulNodes) == len(shard.members) {
			h.lggr.Infow("Successfully sent trigger request to all nodes in shard",
				"shard", shard.donID,
				"legacyExecutionID", legacyExecutionID,
				"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
				"nodeCount", len(shard.members))
			return nil
		}

		// Not all nodes succeeded, wait and retry
		h.lggr.Debugw("Retrying failed nodes for trigger request",
			"shard", shard.donID,
			"legacyExecutionID", legacyExecutionID,
			"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
			"failedCount", len(shard.members)-len(successfulNodes),
			"errors", combinedErr)

		select {
		case <-doneCh:
			h.lggr.Infow("Callback already responded to, stopping retries",
				"shard", shard.donID,
				"legacyExecutionID", legacyExecutionID,
				"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
				"requestID", req.ID,
				"successNodes", len(successfulNodes),
				"totalNodes", len(shard.members))
			return nil
		case <-time.After(b.Duration()):
			continue
		case <-ctx.Done():
			return fmt.Errorf("shard %s: request retry time exceeded, some nodes may not have received the request: legacyExecutionID=%s, executionIDWithTriggerIndex=%s, successNodes=%d, totalNodes=%d",
				shard.donID, legacyExecutionID, executionIDWithTriggerIndex, len(successfulNodes), len(shard.members))
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L2233-2242)
```go
	// Late response from shard 0 must be rejected: callback was already cleaned up.
	err = handler.HandleNodeTriggerResponse(t.Context(), nodeResp, "n1")
	require.Error(t, err)
	require.Contains(t, err.Error(), "callback not found")

	// Callback should no longer exist.
	handler.callbacksMu.Lock()
	_, exists := handler.callbacks[req.ID]
	handler.callbacksMu.Unlock()
	require.False(t, exists)
```
