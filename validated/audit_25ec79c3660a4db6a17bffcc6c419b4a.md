## Finding: Global request-ID keyspace in the HTTP Trigger Handler allows an unprivileged caller to block other users' workflow-trigger requests

### Title
Unprivileged Client Can Block Other Users' `workflows.execute` HTTP-Trigger Requests via Global RequestID Collision - (File: `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`)

### Summary
The Gateway's HTTP Trigger Handler indexes all in-flight user requests in a single map keyed only by the client-supplied JSON-RPC `id`, with no scoping by workflow, workflow owner, or caller identity. Any unprivileged caller can occupy a given `id` value indefinitely by re-submitting requests as soon as the previous one expires, permanently denying service to any other (potentially unrelated) caller who happens to use the same `id`.

### Finding Description
`httpTriggerHandler.callbacks` is declared as `map[string]savedCallback // requestID -> savedCallback` [1](#0-0) , i.e. a single global namespace shared by every workflow and every caller reaching the gateway's internet-facing HTTP trigger endpoint.

`validateRequestID` only rejects empty IDs or IDs containing `/`; it performs no scoping, uniqueness guarantee, or per-caller/per-workflow namespacing: [2](#0-1) 

`setupCallback` inserts into this global map, and rejects the request outright if the exact `id` string is already in flight for *any* other workflow/caller: [3](#0-2) 

The entry is only removed either when the workflow DON successfully responds (`cleanupCallback` in `HandleNodeTriggerResponse`) or by the periodic reaper after `CleanUpPeriodMs` elapses: [4](#0-3) [5](#0-4) 

This mirrors the bug class in the external report: a strict, unscoped state check (`_, found := h.callbacks[requestID]`) that any unprivileged party can manipulate/occupy, and which an attacker can repeatedly "front-run" — reoccupying the slot the instant it clears (either by getting a fast rejection response, or waiting exactly `CleanUpPeriodMs`) — to keep the block persistent, exactly as described for `payFastLane`/`UpdateManager.update`.

### Impact Explanation
Because `id` values are commonly predictable/low-cardinality (many JSON-RPC client libraries default to sequential integers like `"1"`, `"2"`, or callers may reuse simple/short IDs), an attacker with no privileges beyond being able to call the gateway's HTTP trigger endpoint can:
1. Submit a `workflows.execute` request (for any workflow they can target/authorize against, even their own) using a commonly-used `id`.
2. Hold/occupy that `id` in the global `callbacks` map.
3. As soon as it's cleaned up (on completion or reaper timeout), immediately resubmit with the same `id`.

Any other legitimate caller whose client happens to pick that same `id` value will have their request rejected with `jsonrpc.ErrConflict` ("requestID: %s has already been used") [6](#0-5) , denying them workflow execution — a denial-of-service against unrelated users/workflows through the internet-facing gateway, with no authentication bypass required to cause the collision (the attacker only needs to be an authorized-enough caller for their own target workflow, not the victim's).

### Likelihood Explanation
Exploitability depends on ID collision, which is probabilistic unless the attacker can predict/observe victim ID patterns (e.g., simple counters, timestamps, or well-known conventions used by common client SDKs). This makes it a lower-severity but concrete, unprivileged-actor-reachable DoS/state-pollution primitive rather than a guaranteed compromise — the root cause (global, unscoped keyspace with no per-owner/per-workflow partition) is confirmed in code, but real-world impact scales with how predictable caller-chosen `id`s are in practice.

### Recommendation
Scope the in-flight request registry by workflow ID (and/or authorized caller/owner) in addition to the raw client `id`, e.g. key the `callbacks` map by a composite `(workflowID, requestID)` or `(callerKey, requestID)` tuple instead of `requestID` alone, so that one caller/workflow cannot collide with or block another's request namespace.

### Proof of Concept
1. Attacker sends `workflows.execute` JSON-RPC request to the gateway HTTP trigger endpoint with `id: "1"`, targeting a workflow they are authorized to invoke.
2. `setupCallback` inserts `callbacks["1"] = ...` [7](#0-6) .
3. A legitimate, unrelated user's client (using a default/incrementing JSON-RPC id scheme) sends its own `workflows.execute` request with `id: "1"` targeting a different workflow.
4. `setupCallback` sees `callbacks["1"]` already present and rejects the legitimate request with `ErrConflict`, even though the two requests are for entirely different workflows/owners [6](#0-5) .
5. Attacker repeats step 1 immediately after each cleanup interval (`CleanUpPeriodMs`) to keep `id: "1"` perpetually occupied, continuously denying service to any caller using that ID.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L59-60)
```go
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
```

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L426-433)
```go
	doneCh := make(chan struct{})
	h.callbacks[requestID] = savedCallback{
		Callback:            callback,
		requestStartTime:    requestStartTime,
		createdAt:           time.Now(),
		responseAggregators: aggregators,
		doneCh:              doneCh,
	}
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
