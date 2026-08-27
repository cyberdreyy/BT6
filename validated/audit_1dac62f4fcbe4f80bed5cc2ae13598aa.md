### Title
`reapExpiredCallbacks` reaps in-flight callbacks using `CleanUpPeriodMs` instead of `MaxTriggerRequestDurationMs`, silently dropping and freeing `requestID`s for cross-user response misattribution - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`reapExpiredCallbacks` expires a callback if `now.Sub(callback.createdAt) > CleanUpPeriodMs`, not `MaxTriggerRequestDurationMs`, even though `sendWithRetries`/`sendToShard` are still actively retrying under a separate `ctxWithTimeout` bound by `MaxTriggerRequestDurationMs`. This lets the reaper delete `h.callbacks[requestID]` and close `doneCh` long before the in-flight send loop's real deadline, silently terminating delivery without ever calling the user's `SendResponse`, and freeing the `requestID` key for reuse by a second, unrelated caller while the first request's shard sends and late node responses are still in flight.

### Finding Description
`setupCallback` stamps `createdAt = time.Now()` when the callback is registered [1](#0-0) , and the background reaper ticks every `CleanUpPeriodMs` and expires entries strictly against `CleanUpPeriodMs`, never referencing `MaxTriggerRequestDurationMs`: [2](#0-1) . Meanwhile `sendWithRetries` derives its own independent deadline from `MaxTriggerRequestDurationMs` via `ctxWithTimeout` [3](#0-2) , and `sendToShard` keeps retrying against shard members until `doneCh` closes or that context is done [4](#0-3) .

If `CleanUpPeriodMs < MaxTriggerRequestDurationMs` (there is no code enforcing the opposite ordering), the reaper's `cleanupCallback` fires first: it closes `doneCh` and deletes the map entry [5](#0-4)  without ever invoking `callback.SendResponse` or `handleUserError`. The still-running `sendToShard` goroutines observe the closed `doneCh` in their `select` and return `nil` as if the request had been "already responded to" [6](#0-5) , so the caller silently gets no success and no failure notification — the user's original HTTP/gateway request effectively hangs or times out at a layer above with no diagnostic signal, while the gateway internally believes cleanup succeeded.

Because the map entry is deleted, `setupCallback`'s uniqueness check `if _, found := h.callbacks[requestID]; found` no longer blocks reuse [7](#0-6) , allowing a second caller to register a brand-new `savedCallback` (new `doneCh`, new `responseAggregators`) under the identical `requestID` string while the first caller's shard-send goroutines and any late DON node responses for the original request are still outstanding (their context isn't cancelled until `MaxTriggerRequestDurationMs`). When those late node responses arrive, `HandleNodeTriggerResponse` looks them up purely by `resp.ID` [8](#0-7)  — which now resolves to the second caller's `savedCallback` — and feeds them into the second caller's `responseAggregators`, potentially causing the second caller to receive an aggregated response built (in whole or part) from the first caller's execution.

There is no code that scopes `requestID` per caller/workflow-owner beyond the momentary in-flight uniqueness check, and no re-validation on `HandleNodeTriggerResponse` that the response actually corresponds to the same workflow/owner that registered the current `savedCallback` for that ID.

### Impact Explanation
This is a cross-user response confusion issue: a legitimate user's job never gets a completion/failure signal (silent request loss), and — if a second unrelated caller reuses the same `requestID` while the first request's late shard responses are still arriving — that second caller can receive response payload data originating from the first caller's workflow execution, matching the "cross-user response confusion / attacker-controlled data returned to another user" impact class. Impact is bounded to response-delivery integrity/confidentiality between two request submitters sharing the gateway's HTTP trigger handler; it does not itself grant privilege escalation or fund movement.

### Likelihood Explanation
Exploitation requires: (1) the deployment's `CleanUpPeriodMs` to be configured shorter than `MaxTriggerRequestDurationMs` (plausible/likely given they are independent config knobs with no enforced ordering — I could not fully verify the operational defaults in `http_handler.go` config wiring due to index/tool limits), (2) a first request that runs long enough to still be in-flight past `CleanUpPeriodMs` but before `MaxTriggerRequestDurationMs`, and (3) a second caller choosing/guessing the exact same `requestID` string within that window. No elevated privileges are required — any unauthenticated/low-privilege gateway client can submit a trigger request with an attacker-chosen `requestID`; the main constraint is knowing or predicting the victim's `requestID`, which is client-supplied and not always secret or unpredictable (e.g., sequential or workflow-derived IDs). This makes the silent-failure-on-reap part of the bug reliably reproducible, while the requestID-collision cross-user-leak part is conditional on ID predictability.

### Recommendation
- Base `reapExpiredCallbacks`'s expiry check on `MaxTriggerRequestDurationMs` (the caller's actual deadline) rather than `CleanUpPeriodMs`, or track a per-callback deadline (`createdAt + MaxTriggerRequestDurationMs`) set at `setupCallback` time and compare against that.
- When reaping, actively notify the caller via `handleUserError`/`SendResponse` (e.g., an internal/timeout error) before calling `cleanupCallback`, instead of silently closing `doneCh`.
- Scope the `callbacks` map key by `(requestID, workflowOwner)` or another caller-bound identifier, not `requestID` alone, so requestID reuse across different callers cannot collide.

### Proof of Concept
Go unit test in `http_trigger_handler_test.go`:
1. Configure `CleanUpPeriodMs` = 50ms and `MaxTriggerRequestDurationMs` = 5000ms.
2. Register `callback1` via `setupCallback` for `requestID = "X"`, then start a goroutine calling `sendToShard` with a shard whose `connMgr.SendToNode` always errors (forcing retries).
3. Advance time / wait > `CleanUpPeriodMs`, invoke `reapExpiredCallbacks` directly; assert:
   - `h.callbacks["X"]` no longer exists,
   - `callback1`'s `SendResponse` mock was never called (silent drop),
   - the `sendToShard` goroutine returns `nil` (via closed `doneCh`) instead of a timeout error, well before `MaxTriggerRequestDurationMs` elapses.
4. Immediately call `setupCallback` again for the same `requestID = "X"` with `callback2` mock; assert it succeeds (no `ErrConflict`).
5. Simulate a late node response for `requestID = "X"` from a node belonging to `callback1`'s original shard via `HandleNodeTriggerResponse`; assert it is aggregated into `callback2`'s `responseAggregators` and, once quorum is reached, `callback2.SendResponse` is invoked with data — demonstrating the cross-user response delivery. Add an assertion that this should never happen (i.e., the test should currently fail, confirming the vulnerability) by checking `callback2`'s received response is not derived from `callback1`'s request content.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L448-456)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L526-538)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L624-634)
```go
	// Create a context that will be cancelled when the max request duration is reached
	maxDuration := time.Duration(h.config.MaxTriggerRequestDurationMs) * time.Millisecond
	ctxWithTimeout, cancel := context.WithTimeout(ctx, maxDuration)
	defer cancel()

	// Run one send loop per assigned shard in parallel.
	errCh := make(chan error, len(assigned))
	for _, shard := range assigned {
		h.wg.Go(func() {
			errCh <- h.sendToShard(ctxWithTimeout, shard, legacyExecutionID, executionIDWithTriggerIndex, req, doneCh)
		})
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L725-740)
```go
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
```
