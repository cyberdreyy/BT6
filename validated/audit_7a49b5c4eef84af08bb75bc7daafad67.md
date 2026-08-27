### Title
Global unscoped `requestID` keyspace in `h.callbacks` allows cross-workflow requestID collision causing legitimate requests to be silently dropped (cross-tenant DoS) - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`httpTriggerHandler.callbacks` is a single `map[string]savedCallback` keyed only by the caller-supplied `requestID`, with no workflowID or authorized-key component in the key. Two different, independently authorized workflows that happen to choose the same `requestID` (e.g. both use "1") can collide: whichever request reaches `setupCallback` second is rejected with `jsonrpc.ErrConflict` and never dispatched to any node, causing a silent denial-of-service against a fully legitimate, unrelated request.

### Finding Description
`HandleUserTriggerRequest` validates and authorizes the request per-workflow (`validatedTriggerRequest`, `resolveWorkflowID`, `authorizeRequest`, `checkRateLimit`) but then calls `h.setupCallback(ctx, req.ID, callback, requestStartTime, workflowID)` using the raw, user-controlled `req.ID` as the sole map key: [1](#0-0) 
The lookup and insertion (`h.callbacks[requestID]`) do not incorporate `workflowID`, `workflowOwner`, or the authorized key at all, so the map's namespace is global across all tenants/workflows hitting this gateway node. `validateRequestID` only rejects empty strings and strings containing `/`, and does not enforce any per-workflow/global uniqueness scheme (e.g. UUID) that would make accidental or attacker-induced collisions unlikely: [2](#0-1) 
When two authorized requests for different workflows use the same `requestID` concurrently, the second call to `setupCallback` finds the key already present and returns `ErrConflict`, and the request is never forwarded via `sendWithRetries`/`sendToShard`, meaning that workflow's execution is dropped outright rather than merely delayed.
The same unscoped keying is used symmetrically in `HandleNodeTriggerResponse` (`h.callbacks[resp.ID]`), reinforcing that the whole callback lifecycle assumes requestID is globally unique across all workflows on the gateway.

### Impact Explanation
This is a cross-tenant availability issue: an authorized user of one workflow can, by mere accidental collision (or deliberate low-cost spamming of common IDs such as `"1"`, `"2"`, sequential counters) cause a legitimate, unrelated request from a different authorized workflow to be rejected and dropped without ever reaching the DON. This matches the "cross-tenant collision/DoS" impact class raised in the question — a workflow's own request never gets sent for execution simply because another unrelated tenant chose the same string ID. The information-leak portion of the question is not substantiated: the `ErrConflict` message is returned only to the same request that lost the race (`callback.SendResponse` uses the `callback` value belonging to that same requester), and its text ("requestID: %s has already been used...") does not disclose the other workflow's identity, owner, or content, so no cross-user data or metadata is actually exposed.

### Likelihood Explanation
Requires two distinct valid JWTs/authorized keys for two different workflows submitting requests concurrently with an identical `requestID`. Because the system does not mandate any uniqueness scheme (e.g., UUIDs) for `requestID`, and many client integrations naturally use simple/sequential counters ("1", "2", ...), accidental collisions across independently-operated workflows are plausible, especially as the number of workflows on a shared gateway grows. An attacker could also increase the odds deliberately by continuously issuing requests with common ID values under their own authorized workflow, without needing any elevated privilege — just a valid key for their own workflow.

### Recommendation
Scope the callback map key by both `workflowID` (or the authorized key) and `requestID`, e.g. `fmt.Sprintf("%s/%s", workflowID, requestID)`, mirroring the existing internal node-routing convention (`http_action/{workflowID}/{uuid}`) already reserved via the `/`-rejection in `validateRequestID`. Apply the same composite key consistently in `setupCallback`, `cleanupCallback`, `reapExpiredCallbacks`, and `HandleNodeTriggerResponse` so that requestID uniqueness is only required within a single workflow's namespace, not globally across all tenants.

### Proof of Concept
Go handler-level test in `http_trigger_handler_test.go`:
1. Construct an `httpTriggerHandler` with a `workflowMetadataHandler` configured with two distinct valid workflows, `workflowA` and `workflowB`, each with valid authorized keys/shards.
2. Concurrently call `HandleUserTriggerRequest` twice: once for `workflowA` with `req.ID = "1"`, once for `workflowB` with `req.ID = "1"`, using separate mock `handlers.Callback` instances to capture each response.
3. Serialize the two calls so the first acquires `callbacksMu` and inserts into `h.callbacks["1"]`, then release and let the second proceed.
4. Assert: the first request proceeds to `sendWithRetries` (no error), and the second callback receives a `jsonrpc.WireError` with `Code == jsonrpc.ErrConflict` even though it targets a completely different, correctly-authorized `workflowB`, demonstrating the request for `workflowB` is dropped without ever being dispatched to any shard/node.
5. Additionally assert the error message text does not contain `workflowA`'s ID/owner, confirming no cross-workflow detail is exposed (to distinguish DoS-only impact from an information-leak claim).

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
