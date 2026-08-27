### Title
Reused requestID after callback expiry causes stale DON responses to be delivered to an unrelated new callback - (core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`httpTriggerHandler` correlates DON node responses to a waiting HTTP caller purely via the caller-supplied `requestID` string, with no per-execution nonce or generation counter. Once `reapExpiredCallbacks` deletes an expired entry from `h.callbacks`, the same `requestID` can immediately be reused by `setupCallback` for a brand-new, unrelated request; a late-arriving node response tied to the original (expired) execution will then be matched, aggregated, and delivered to the new callback holder.

### Finding Description
`setupCallback` [1](#0-0)  stores callbacks keyed only by the client-controlled `requestID` (`req.ID`), which is only checked for non-emptiness and absence of `/` in `validateRequestID` [2](#0-1) . The outbound JSON-RPC request sent to DON nodes reuses this exact same ID as the wire correlation key (`req.ID` is forwarded via `reqWithAuthorizedKey`) [3](#0-2) , so the only thing binding an inbound `HandleNodeTriggerResponse` to "this specific execution" is the map key `resp.ID` [4](#0-3) .

`reapExpiredCallbacks` runs periodically (every `CleanUpPeriodMs`), and for any callback older than `CleanUpPeriodMs` it calls `cleanupCallback`, which closes `doneCh` and deletes the map entry [5](#0-4) [6](#0-5) . Immediately after this delete, `setupCallback`'s uniqueness check (`if _, found := h.callbacks[requestID]; found`) no longer blocks a new request reusing the same ID string, so a second, unrelated request can register a brand-new `savedCallback` under the identical key [7](#0-6) .

If a slow/late node response for the *original* (now-expired) execution subsequently arrives, `HandleNodeTriggerResponse` looks up `h.callbacks[resp.ID]`, finds the *new* `savedCallback` (since the key was reused), and — provided the new callback's workflow happens to be assigned to the same shard/donID as the original node — feeds the stale response into that new callback's `IdenticalNodeResponseAggregator`. If quorum is reached, `saved.SendResponse(...)` delivers the original execution's response payload to the new (different) callback holder [8](#0-7) . There is no per-execution nonce, token, or generation counter to detect and reject this "stale response for expired/replaced registration" case; the aggregator (`IdenticalNodeResponseAggregator.CollectAndAggregate`) only checks response-content digest and node identity, not execution provenance [9](#0-8) .

Because all map mutations happen under `callbacksMu` (a `sync.Mutex`), there is no data race in the Go-runtime sense, but the *logical* race described (delete-then-reinsert-then-misroute) is real: nothing in the code prevents a `requestID` from being immediately reissued for a semantically unrelated execution, and nothing tags in-flight DON responses with the specific "generation"/registration they belong to.

### Impact Explanation
This is a cross-user/cross-execution response-confusion bug (Chainlink bounty class: unauthorized disclosure of another user's data / request impersonation). If two distinct authorized callers of the same workflow use overlapping `requestID` schemes (e.g., sequential or predictable IDs rather than random UUIDs — nothing in the code enforces or recommends randomness) and one request happens to expire right as another reuses the ID, the second caller can receive the first caller's execution result instead of their own, and the first caller's response is silently swallowed by the wrong recipient. This violates response isolation and could leak sensitive workflow output across tenants sharing a gateway/workflow.

### Likelihood Explanation
Exploitability requires: (1) the attacker (or an unwitting victim) to know/guess or naturally reuse an identical `requestID` string used by another caller of the same workflow, (2) that original request to be abandoned/starved so it survives to expiry (`CleanUpPeriodMs`), (3) precise timing of the new registration between the reap-triggered delete and the stale response's arrival, and (4) the new registration to be assigned to the same DON shard as the original so the aggregator routes match. This is feasible for a single attacker attacking themselves (no cross-user impact) but requires either colluding/predictable ID schemes or coincidental collision for genuine cross-user impact, making real-world exploitation non-trivial but architecturally real and repeatable given the preconditions (e.g., applications that use non-random, sequential, or otherwise attacker-predictable request IDs).

### Recommendation
Bind the correlation between a registered callback and inbound DON responses to more than just the raw client-supplied `requestID`:
- Include a server-generated, unpredictable per-registration token/nonce (or generation counter) in the outbound request to nodes, and validate that this token/generation matches on `HandleNodeTriggerResponse` before crediting the response to the aggregator.
- Alternatively, salt/derive the internal `h.callbacks` map key from `requestID` + `createdAt` (or a monotonically increasing sequence) rather than reusing the raw client ID, so a reaped entry's key can never collide with a freshly registered one for a different execution.
- After `cleanupCallback` due to expiry, optionally keep a short-lived "tombstone" for the ID (with a lower TTL) to explicitly reject any late response and prevent it from being misrouted if a new registration reuses the same ID within the tombstone window.

### Proof of Concept
Go unit test plan (in `http_trigger_handler_test.go`):
1. Build an `httpTriggerHandler` with a very small `CleanUpPeriodMs` (e.g., 1ms) and a single shard with one member node.
2. Call `setupCallback(ctx, "X", callbackA, ...)` for workflow W1 (assigned to shard S1) to register execution A; do not close/respond it.
3. Sleep past `CleanUpPeriodMs`, then invoke `h.reapExpiredCallbacks(ctx)` directly to simulate the ticker firing, confirming `h.callbacks["X"]` no longer exists and `callbackA`'s `doneCh` is closed.
4. Immediately call `setupCallback(ctx, "X", callbackB, ...)` for workflow W2 (also assigned to shard S1) to register execution B, and confirm success (no `ErrConflict`).
5. Simulate a late DON response for the *original* execution A by calling `h.HandleNodeTriggerResponse(ctx, &jsonrpc.Response[json.RawMessage]{ID: "X", Result: originalPayload}, nodeAddrOfS1)` enough times from shard S1 members to reach quorum.
6. Assert that `callbackB.SendResponse` is invoked with `originalPayload` (proving cross-execution misdelivery) OR, after the fix, assert that `HandleNodeTriggerResponse` returns an error such as "stale/mismatched response for request ID" and `callbackB` never receives `originalPayload`.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L744-759)
```go
func reqWithAuthorizedKey(req *jsonrpc.Request[gateway_common.HTTPTriggerRequest], key gateway_common.AuthorizedKey) (*jsonrpc.Request[json.RawMessage], error) {
	params := *req.Params
	params.Key = key
	msg, err := json.Marshal(params)
	if err != nil {
		return nil, errors.New("error marshaling trigger request")
	}
	rawMsg := json.RawMessage(msg)
	r := &jsonrpc.Request[json.RawMessage]{
		Version: req.Version,
		ID:      req.ID,
		Method:  gateway_common.MethodWorkflowExecute,
		Params:  &rawMsg,
	}
	return r, err
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
