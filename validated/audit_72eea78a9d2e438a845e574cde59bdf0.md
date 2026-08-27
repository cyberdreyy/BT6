### Title
Reaped requestID can be silently reused, allowing a stale in-flight DON response from an attacker's expired request to be aggregated into a victim's new request with the same ID - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`setupCallback` only guards against *simultaneous* reuse of a `requestID` (map lookup at [1](#0-0) ), but once `reapExpiredCallbacks` deletes the entry via `cleanupCallback` ( [2](#0-1)  and [3](#0-2) ), the same `requestID` string can be immediately reused by any subsequent caller. `HandleNodeTriggerResponse` looks up the callback purely by `resp.ID` string with no per-session/nonce binding ( [4](#0-3) ), so a stale response belonging to the reaped (attacker) request can be routed into the freshly-created aggregator of a legitimate victim's request that happens to reuse the same ID and shares an overlapping shard/DON.

### Finding Description
`setupCallback` builds a brand-new `map[string]*aggregation.IdenticalNodeResponseAggregator` keyed by shard `donID` for every call, and stores it under `h.callbacks[requestID]` after only checking that no entry currently exists for that ID ( [5](#0-4) ). There is no tombstone, generation counter, or per-attempt nonce distinguishing one "use" of a `requestID` string from a prior, now-expired use of the exact same string.

`sendToShard`, which fans the request out to shard nodes and retries on failure, only stops retrying when it observes the closed `doneCh` in its `select` at the bottom of its retry loop ( [6](#0-5) ). Crucially, this only prevents the *gateway* from resending further requests — it cannot recall messages already delivered to shard nodes. Once `SendToNode` succeeds for a node, that node will independently execute the workflow and push a `jsonrpc.Response` back at its own pace; nothing prevents that response from arriving after the gateway has already reaped the callback and reused the `requestID` for a new, unrelated legitimate request.

When that stale response arrives, `HandleNodeTriggerResponse` does:
```go
saved, exists := h.callbacks[resp.ID]
...
agg, ok := saved.responseAggregators[shard.donID]
...
aggResp, err := agg.CollectAndAggregate(resp, nodeAddr)
``` [7](#0-6) 

Since `saved` is now the *victim's* new `savedCallback` (same `requestID` string, different logical request), the stale response is fed into the victim's aggregator for that shard. `IdenticalNodeResponseAggregator.CollectAndAggregate` only tracks response digests and node addresses ( [8](#0-7) ) — it has no concept of which logical request/session it belongs to. If enough stale, mutually-identical responses from the attacker's original (still-retrying/in-flight) request arrive from distinct shard nodes before the victim's genuine nodes reach quorum, `CollectAndAggregate` will return a non-nil aggregated response built from the attacker's stale data, which is then marshaled and delivered to the victim via `saved.SendResponse(...)` ( [9](#0-8) ) — attacker-influenced data returned as if it were the victim's own workflow execution result.

Preconditions required for real exploitation:
- The attacker fully controls `req.ID` before any auth check runs (`validateRequestID` only rejects empty IDs or those containing `/`) ( [10](#0-9) ).
- The victim's subsequent request must reuse the exact same ID string as the attacker's expired request (the gateway does not scope requestIDs by caller/session, so if the client-side ID scheme is predictable/sequential/attacker-influenced, this is feasible).
- The victim's workflow must be assigned to at least one shard/DON overlapping with the attacker's original workflow's shard, so the `donID` key exists in `saved.responseAggregators` for the reused entry.
- The attacker's original shard nodes must still be retrying/responding near the exact reap boundary, which is a real race but plausible given `CleanUpPeriodMs`-based expiry and node-side processing/retry delays.

### Impact Explanation
This is a cross-request response confusion vulnerability: a victim application can receive a workflow-execution response that was actually produced for an attacker-controlled workflow/request, rather than their own. Depending on what the victim does with the returned payload (e.g., trusts it as ground truth for downstream automation, on-chain triggers, or financial logic), this can lead to unauthorized/incorrect actions taken on attacker-influenced data — matching the "cross-user response confusion" impact class called out in the scope.

### Likelihood Explanation
Exploitation requires precise timing (ID reuse must happen within a narrow race window around reap) and requires the victim to reuse a predictable/attacker-guessable `requestID`, plus shard/DON overlap between the two workflows. These are non-trivial but plausible preconditions for an unauthenticated/unprivileged caller — no operator/node-level access, no auth bypass, no malicious node required. Likelihood is moderate-to-low due to the timing race, but it is deterministically reproducible in a unit test that directly manipulates `h.callbacks` state and calls `setupCallback`/`cleanupCallback`/`HandleNodeTriggerResponse` in the described order.

### Recommendation
Bind each callback entry to a unique, unforgeable session token rather than the raw client-supplied `requestID` string alone (e.g., an internal generation counter or UUID stored alongside `requestID`, and echoed/verified on every routed node response before feeding it into `CollectAndAggregate`). Alternatively, when `cleanupCallback`/`reapExpiredCallbacks` removes an entry, retain a short-lived tombstone (with an epoch/generation number) for that `requestID` so that any late-arriving `HandleNodeTriggerResponse` for the old generation is rejected instead of silently falling through to a newly created entry with the same ID.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go`:
1. Call `setupCallback(ctx, "X", callbackA, t0, workflowA)` where `workflowA` is assigned to shard `donID="shard1"`.
2. Directly invoke `h.reapExpiredCallbacks(ctx)` after advancing time past `CleanUpPeriodMs`, confirming `doneCh` for A is closed and `h.callbacks["X"]` is deleted.
3. Call `setupCallback(ctx, "X", callbackB, t1, workflowB)` where `workflowB` is also assigned to `donID="shard1"`, succeeding (no conflict error) and installing a fresh aggregator for shard1.
4. Simulate stale in-flight node responses from workflowA's shard1 members by calling `h.HandleNodeTriggerResponse(ctx, respFromAttackerWorkflow, nodeAddr)` (with `resp.ID = "X"`, digest matching attacker's stale payload) enough times to reach shard1's quorum threshold.
5. Assert that `callbackB.SendResponse` (the victim's callback) is invoked with the attacker's stale payload, and that `callbackB`'s legitimate genuine node responses (if simulated afterward) are ignored because `cleanupCallback` already fired for "X" — proving cross-request contamination occurred with no isolation between the two "sessions" that shared the same `requestID`.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L439-446)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L481-496)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L527-544)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L717-741)
```go
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
