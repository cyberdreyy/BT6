### Title
Cross-workflow response confusion via reused/collided request IDs in gateway HTTP trigger callback map - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
The `httpTriggerHandler` correlates asynchronous node trigger responses to pending user requests solely by the client-supplied JSON-RPC `id` (`h.callbacks[requestID]`), with no binding to the requesting workflow, owner, or auth key. Because `validateRequestID` only rejects empty IDs or IDs containing `/`, and `setupCallback` only blocks *concurrent* reuse (rejecting only while an entry for that ID still exists), an attacker who registers their own workflow can capture a delayed node response that actually belongs to a different workflow's earlier (already-cleaned-up) request, receiving that victim workflow's execution output through their own callback.

### Finding Description
`setupCallback` stores pending requests keyed only by `requestID` [1](#0-0) , and only rejects a `HandleUserTriggerRequest` call with `jsonrpc.ErrConflict` if an entry for that exact ID is *still present* [2](#0-1) . Once a callback is removed — either by `cleanupCallback` after a shard reaches quorum [3](#0-2)  or by `reapExpiredCallbacks` after `CleanUpPeriodMs` [4](#0-3)  — the same `requestID` string becomes free for any other user/workflow to claim.

`HandleNodeTriggerResponse` then resolves the target callback purely by `resp.ID`: `saved, exists := h.callbacks[resp.ID]` [5](#0-4) . It verifies only that the responding node belongs to a shard the *currently stored* callback's workflow is assigned to (`saved.responseAggregators[shard.donID]`) [6](#0-5)  — it never checks that the response actually originated from the same workflow/execution that created the entry. `validateRequestID` provides no uniqueness guarantee beyond concurrent collisions, and does not bind the ID to a workflowID/owner [7](#0-6) .

Exploit flow:
1. Victim (or any workflow W1) triggers a request with `id = "X"`; nodes in shard S are sent the request via `sendWithRetries`/`sendToShard`.
2. W1's callback for `"X"` is removed either because quorum was reached elsewhere (multi-shard race, see `TestHttpTriggerHandler_MultiShardQuorumRace`, which explicitly documents that late shard responses after cleanup are dropped with "callback not found") [8](#0-7) , or the reaper expires it after `CleanUpPeriodMs`, while one or more slow nodes in shard S have not yet replied.
3. Attacker, who is authorized only for their own workflow W2 (which is also served by shard S, e.g., shared DON/shard), immediately submits a new trigger request reusing `id = "X"`. `setupCallback` accepts it because no entry currently exists for `"X"`.
4. When the victim's straggling node responses for shard S subsequently arrive with `resp.ID = "X"`, `HandleNodeTriggerResponse` finds the attacker's freshly-created callback entry (not the victim's, which is gone) and, since the attacker's workflow is also assigned to shard S, `saved.responseAggregators[shard.donID]` succeeds and the stray responses are aggregated into the attacker's callback.
5. Once threshold is reached, `saved.SendResponse` delivers the victim's workflow output data to the attacker's callback [9](#0-8) .

No existing check (auth, shard membership, or request-ID uniqueness) binds a node response to the specific workflow/owner/execution that generated the original request ID — only to "some" currently-cached entry under that literal string.

### Impact Explanation
This allows an unprivileged, self-authorized workflow caller to receive execution response payloads belonging to another user's/workflow's trigger request purely by reusing that request's `id` after it (or one shard's leg of it) has been cleaned up but before all shard nodes have replied. This is a cross-user response confusion / unauthorized disclosure of another workflow's execution output, matching the "cross-user response confusion" bounty impact class. Depending on what data the HTTP trigger workflow output carries, this can leak sensitive computed data intended for a different workflow owner.

### Likelihood Explanation
Exploitation requires: (a) attacker has a valid signed key for their own registered workflow served by a shard also used by the victim (plausible in shared-DON, multi-tenant gateway deployments), (b) attacker knows or can predict/guess the victim's chosen JSON-RPC request `id` (feasible if IDs are low-entropy, sequential, or externally observable, e.g., via shared client libraries, logs, or webhook echoes), and (c) winning a timing race immediately after the victim's callback is removed but before all shard nodes' late responses arrive — easier when relying on `reapExpiredCallbacks`, since the expiry window (`CleanUpPeriodMs`) is a fixed, discoverable interval. This is a race-dependent but concretely reproducible flaw rooted in the non-namespaced `h.callbacks` key design rather than a one-off misconfiguration.

### Recommendation
Namespace the callback map key by `(workflowID, workflowOwner, requestID)` (or an internally generated unique execution ID) instead of the raw client-supplied `requestID` alone, and have `HandleNodeTriggerResponse` verify the response's originating workflow/execution matches the stored callback's workflow before aggregating, not just shard membership.

### Proof of Concept
Go handler-level integration test plan (extending `http_trigger_handler_test.go`):
1. Create a multi-workflow, shared-shard test handler similar to `createMultiShardTriggerHandler`, registering workflow W1 (owner A) and workflow W2 (owner B) both assigned to shard S with 3 nodes, threshold 2.
2. Send `HandleUserTriggerRequest` for W1 with `id = "shared-id"`; have 1 of 3 shard-S nodes ACK immediately (below threshold), leaving the callback pending.
3. Force expiry: advance/mock time past `CleanUpPeriodMs` and call `reapExpiredCallbacks`, removing W1's callback for `"shared-id"` while nodes 2 and 3 of shard S have not yet responded.
4. Send `HandleUserTriggerRequest` for W2 (owner B) reusing `id = "shared-id"`; assert it succeeds (no `ErrConflict`).
5. Deliver the two outstanding node responses (originally destined for W1's execution) with `resp.ID = "shared-id"` via `HandleNodeTriggerResponse` from the two remaining shard-S nodes.
6. Assert W2's callback (`callback.Wait`) receives `api.NoError` and a `RawResponse` equal to the payload that was meant for W1's execution — demonstrating cross-workflow data delivery — while W1 never receives any response (already reaped).

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L456-467)
```go

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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L476-487)
```go
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
