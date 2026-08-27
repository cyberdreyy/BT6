### Title
Cross-tenant requestID collision causes denial-of-service for unrelated workflow's HTTP trigger request - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`httpTriggerHandler.setupCallback` stores in-flight callbacks in a single global map `h.callbacks` keyed only by the user-supplied `requestID`, with no scoping by workflow ID or owner. Any caller authorized for *any* workflow can pre-register a `requestID` that a victim caller of an *unrelated* workflow is about to use, causing the victim's legitimate request to be rejected with `ErrConflict`.

### Finding Description
`HandleUserTriggerRequest` (`http_trigger_handler.go:88-140`) validates the request, resolves the target `workflowID`, and authorizes the caller against that workflow via `h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)` (`workflow_metadata_handler.go:80-108`). Authorization only checks that the caller's JWT signer is registered for the specific `workflowID` being targeted — it does not, and cannot, tie the `requestID` (`req.ID`) namespace to that workflow.

The in-flight request registry is a single package-level map:
```go
callbacks map[string]savedCallback // requestID -> savedCallback
``` [1](#0-0) 

`setupCallback` checks for collisions purely on `requestID`, with no workflow/owner component in the key:
```go
if _, found := h.callbacks[requestID]; found {
    h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, ...)
    return nil, fmt.Errorf("in-flight request ID: %s", requestID)
}
``` [2](#0-1) 

`requestID` is fully attacker/client controlled — `validateRequestID` only rejects empty strings and `/` characters, imposing no uniqueness scope or unpredictability requirement (`http_trigger_handler.go:183-195`). Because authorization is workflow-scoped but the callback map key is global, an attacker who is a legitimately authorized caller of workflow A (their own workflow, requiring only a valid JWT for that workflow per `Authorize`) can submit a `workflows.execute` request with a `requestID` value they predict a victim of unrelated workflow B will use next (e.g., sequential counters, timestamp-derived IDs, or any ID scheme with attacker-guessable determinism). The attacker's own authorized request occupies the `h.callbacks[requestID]` slot until the DON responds or the callback is reaped after `CleanUpPeriodMs`. When the victim's legitimate workflow B request with the same `requestID` arrives, `setupCallback` finds the key already present and returns `ErrConflict`, so the victim's `sendWithRetries` call and downstream node dispatch never happen — the request is silently dropped from the victim's perspective (they get a 409-style conflict rather than execution).

Because the collision window persists until `reapExpiredCallbacks` deletes it (bounded by `CleanUpPeriodMs`) or the attacker's own request completes, this is a real availability-affecting cross-tenant collision, not merely a self-inflicted client error, since the two colliding requests belong to different workflows/owners and no cross-checking of workflow identity is performed before the conflict check.

### Impact Explanation
This is a griefing/denial-of-service primitive: an unprivileged caller holding valid credentials for any single workflow can block or delay execution requests belonging to a completely different, unrelated workflow/owner, without needing any privilege on the victim's workflow. This does not leak the attacker any of the victim's data (the aggregator's shard mapping is tied to the attacker's own workflow, so the victim's DON response cannot be delivered back to the attacker's callback), but it does deny service and could be used to grief specific known/predictable requestID patterns, disrupting availability of another tenant's workflow executions.

### Likelihood Explanation
Exploitability requires only: (1) a valid JWT for any workflow the attacker is authorized on (a low bar — attackers are expected to be authorized on their own workflows), and (2) the ability to predict or brute-force the victim's `requestID` before the victim submits it. Feasibility depends heavily on how client applications generate `requestID` values; if IDs are sequential, timestamp-based, or otherwise low-entropy, the attack is straightforward and repeatable at will since there's no rate limiting tied to guessed IDs across workflows (rate limiting is scoped per-workflow via `checkRateLimit`, `http_trigger_handler.go:371-396`, so it doesn't prevent the attacker from repeatedly trying against their own workflow's quota).

### Recommendation
Scope the in-flight callback map key by `(workflowID, requestID)` (or `(workflowOwner, requestID)`) instead of `requestID` alone, e.g. use a composite key such as `workflowID + "/" + requestID` (the code already reserves `/` as an internal separator, `http_trigger_handler.go:188-193`) when inserting into and looking up `h.callbacks`, and route `HandleNodeTriggerResponse` lookups using the same composite key derived from the node response's associated workflow/execution context.

### Proof of Concept
Go handler-level integration test plan:
1. Construct an `httpTriggerHandler` with a `workflowMetadataHandler` populated with two distinct authorized workflows, WF-A (owner Alice, attacker) and WF-B (owner Bob, victim), each assigned to their own shard(s).
2. As Alice, call `HandleUserTriggerRequest` with a `workflows.execute` request targeting WF-A, using JWT authorized for WF-A, and `req.ID = "shared-id-123"`. Assert this call succeeds and occupies `h.callbacks["shared-id-123"]`.
3. Before that callback is resolved/cleaned up, as Bob, call `HandleUserTriggerRequest` with a request targeting WF-B, using a JWT authorized for WF-B, and the same `req.ID = "shared-id-123"`.
4. Assert Bob's call returns an error and that `callback.SendResponse` was invoked with `jsonrpc.ErrConflict` (`http_trigger_handler.go:402-405`), even though Bob's JWT/workflow authorization was valid and unrelated to Alice's workflow.
5. Assert that `sendWithRetries`/`sendToShard` was never invoked for Bob's request (i.e., WF-B's DON nodes never received the `workflows.execute` dispatch), demonstrating the request was dropped, not merely delayed.
6. Additionally simulate `HandleNodeTriggerResponse` from a WF-A shard node with `resp.ID = "shared-id-123"` and assert only Alice's callback receives the response (Bob's request was never registered), confirming isolation failure occurs at admission (conflict) rather than response misdelivery.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L59-60)
```go
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
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
