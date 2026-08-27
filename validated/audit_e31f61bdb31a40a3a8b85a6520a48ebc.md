### Title
Unsynchronized map/channel access in `triggerConnectorHandler.processTrigger` races with `UnregisterTrigger`'s mutex-protected delete/close, causing panics or stale-sender delivery - ([File: core/capabilities/webapi/trigger/trigger.go])

### Summary
`processTrigger` (invoked from `HandleGatewayMessage`) iterates `h.registeredWorkflows` and reads `trigger.allowedSenders`/`trigger.ch` **without holding `h.mu`**, while `RegisterTrigger` and `UnregisterTrigger` mutate the same map and close/remove the channel **under `h.mu`**. A gateway message that arrives concurrently with `UnregisterTrigger` for the same `TriggerID` can be matched against a `webapiTrigger` snapshot obtained before the lock-protected delete, and can then attempt `trigger.ch <- tr` on a channel that `UnregisterTrigger` closes concurrently, or race on `h.registeredWorkflows` itself.

### Finding Description
`HandleGatewayMessage` → `processTrigger` does `for _, trigger := range h.registeredWorkflows` at [1](#0-0)  without acquiring `h.mu`. It then checks `trigger.allowedSenders[sender.String()]` and later performs a blocking send `case trigger.ch <- tr:` at [2](#0-1) .

Meanwhile `UnregisterTrigger` takes the lock, closes the trigger's channel, and deletes the map entry: [3](#0-2) . `RegisterTrigger` similarly mutates the map under the same lock: [4](#0-3) .

Because `processTrigger` never takes `h.mu`, the following races are possible when a gateway message for a `TriggerID` arrives at the same time as `UnregisterTrigger` for that ID:
- Unsynchronized concurrent map iteration (`processTrigger`) and map write (`delete` in `UnregisterTrigger`), which Go's runtime detects as `fatal error: concurrent map iteration and map write` (a hard process crash, not a recoverable panic).
- `processTrigger` may already have obtained the `webapiTrigger` struct (including the still-live `ch` field) via range before `UnregisterTrigger` calls `close(workflow.ch)`. If `processTrigger` subsequently executes `trigger.ch <- tr` after the close happened, the send will panic with `send on closed channel`.

This directly matches the scenario in the question: a sender that was allowed at the time `processTrigger` read `trigger.allowedSenders` (a stale, unsynchronized read taken with no lock at all, not merely "before the delete") can still be matched and the code will attempt delivery on a channel the owner believes has been closed/deregistered, or the whole read/iterate operation races the writer causing a runtime crash.

### Impact Explanation
This is a real concurrency defect reachable purely through gateway traffic (an unprivileged, signed-but-formerly-allowed sender) racing with the normal capability-unregistration lifecycle (workflow deletion/pause triggers `UnregisterTrigger`). The concrete impacts are:
- Node process crash/DoS via unrecovered `fatal error: concurrent map iteration and map write` or `panic: send on closed channel` inside a capability goroutine handling gateway messages — this matches the "Denial of Service" / node-crash impact class.
- Secondary effect: a stale `allowedSenders`/`ch` snapshot means the unregistration invariant ("no delivery to a deregistered workflow") is not atomically enforced with respect to concurrent gateway message processing, since `processTrigger` completely bypasses `h.mu`.

### Likelihood Explanation
Preconditions: the attacker only needs to be a previously-allowed sender for a trigger, and the natural, common operational event of the workflow being deleted/unregistered (which calls `UnregisterTrigger`) needs to overlap in time with an in-flight gateway message for that same `TriggerID`. No additional privilege is required beyond already being an allowed sender, and unregistration is a routine lifecycle event, not something exotic — so the race window opens whenever a workflow is stopped/removed while messages are in flight, which is plausible in production under load. This is a genuine data race confirmed by code inspection (no `h.mu` in `processTrigger`), reproducible with `go test -race`.

### Recommendation
Take `h.mu.RLock()`/`RUnlock()` (or full `Lock`) around the entire body of `processTrigger` that reads `h.registeredWorkflows` and any per-trigger fields (`allowedSenders`, `allowedTopics`, `ch`, `rateLimiter`), matching the locking discipline already used in `RegisterTrigger`/`UnregisterTrigger`. Avoid closing/sending on `ch` while other goroutines may still hold references to it; consider making channel closure only observable through the same lock, or use a per-trigger "closed" flag checked before every send, guarded by the same mutex.

### Proof of Concept
Go test plan (`core/capabilities/webapi/trigger/trigger_test.go`):
1. Build a `triggerConnectorHandler` via `NewTrigger` and call `RegisterTrigger` with an `AllowedSenders` list containing address `A`.
2. Start two goroutines concurrently:
   - Goroutine 1: repeatedly calls `HandleGatewayMessage` with a validly-signed `web_api_trigger` request from sender `A` for the same `TriggerID`.
   - Goroutine 2: calls `UnregisterTrigger` for that `TriggerID`.
3. Run under `go test -race` and assert the test completes without the race detector firing and without a `panic`/`fatal error` (concurrent map iteration/write or send on closed channel).
4. Expected (buggy) result: intermittent `-race` failures on `h.registeredWorkflows` and/or `panic: send on closed channel` from `processTrigger`'s `trigger.ch <- tr`, demonstrating the missing mutex protection in `processTrigger`.

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L97-97)
```go
	for _, trigger := range h.registeredWorkflows {
```

**File:** core/capabilities/webapi/trigger/trigger.go (L99-137)
```go
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
				fullyMatchedWorkflows++
				TriggerEventID := body.Sender + payload.TriggerEventId

				// Emit trigger execution started event
				workflowExecutionID, genErr := events.GenerateExecutionID(trigger.workflowID, TriggerEventID)
				if genErr != nil {
					h.lggr.Errorw("failed to generate execution ID", "err", genErr)
					workflowExecutionID = ""
				}
				emitErr := events.EmitTriggerExecutionStarted(ctx, map[string]string{}, TriggerEventID, workflowExecutionID)
				if emitErr != nil {
					h.lggr.Errorw("failed to emit trigger execution started event", "err", emitErr)
				}

				tr := capabilities.TriggerResponse{
					Event: capabilities.TriggerEvent{
						TriggerType: TriggerType,
						ID:          TriggerEventID,
						Outputs:     wrappedPayload,
					},
				}
				select {
				case <-ctx.Done():
					return nil
				case trigger.ch <- tr:
					// Sending n topics that match a workflow with n allowedTopics, can only be triggered once.
					break
				}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L211-252)
```go
	h.mu.Lock()
	defer h.mu.Unlock()
	_, errBool := h.registeredWorkflows[req.TriggerID]
	if errBool {
		return nil, fmt.Errorf("triggerId %s already registered", req.TriggerID)
	}

	rateLimiterConfig := reqConfig.RateLimiter
	commonRateLimiter := ratelimit.RateLimiterConfig{
		GlobalRPS:      rateLimiterConfig.GlobalRPS,
		GlobalBurst:    int(rateLimiterConfig.GlobalBurst),
		PerSenderRPS:   rateLimiterConfig.PerSenderRPS,
		PerSenderBurst: int(rateLimiterConfig.PerSenderBurst),
	}

	rateLimiter, err := ratelimit.NewRateLimiter(commonRateLimiter)
	if err != nil {
		return nil, err
	}

	allowedSendersMap := map[string]bool{}
	for _, k := range reqConfig.AllowedSenders {
		allowedSendersMap[k] = true
	}

	allowedTopicsMap := map[string]bool{}
	for _, k := range reqConfig.AllowedTopics {
		allowedTopicsMap[k] = true
	}

	ch := make(chan capabilities.TriggerResponse, defaultSendChannelBufferSize)

	h.registeredWorkflows[req.TriggerID] = webapiTrigger{
		workflowID:     req.Metadata.WorkflowID,
		allowedTopics:  allowedTopicsMap,
		allowedSenders: allowedSendersMap,
		ch:             ch,
		config:         *reqConfig,
		rateLimiter:    rateLimiter,
	}

	return ch, nil
```

**File:** core/capabilities/webapi/trigger/trigger.go (L255-266)
```go
func (h *triggerConnectorHandler) UnregisterTrigger(ctx context.Context, req capabilities.TriggerRegistrationRequest) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	workflow, ok := h.registeredWorkflows[req.TriggerID]
	if !ok {
		return fmt.Errorf("triggerId %s not registered", req.TriggerID)
	}

	close(workflow.ch)
	delete(h.registeredWorkflows, req.TriggerID)
	return nil
}
```
