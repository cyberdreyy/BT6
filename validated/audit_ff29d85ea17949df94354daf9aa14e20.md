### Title
Cross-user response confusion via attacker-controlled MessageId collision in savedCallbacks map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores each incoming trigger request's callback in a shared `h.savedCallbacks` map keyed solely by the client-supplied `msg.Body.MessageId`, with no uniqueness check and no binding to the requester's identity. A low-privileged client can pick (or predict) a `MessageId` colliding with another in-flight request; the later store silently overwrites the earlier callback entry, causing the node's response for the *first* request to be delivered to the *second* requester's callback when `handleWebAPITriggerMessage` looks it up, leaking one user's response content (which may include their original request/URL/params) to another user and orphaning the first user's callback.

### Finding Description
`MessageId` originates entirely from client input: in `gateway.go`'s `ProcessRequest` the only server-side check is a length limit (`len(jsonRequest.ID) > 200`) [1](#0-0) , and for JSON-RPC requests `ValidatedMessageFromReq` copies `req.ID` directly into `m.Body.MessageId` with no uniqueness enforcement [2](#0-1) .

In `HandleLegacyUserMessage`, after validating the payload, the handler unconditionally writes into the shared map:
```
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [3](#0-2) 

There is no check for an existing entry with the same key (unlike the newer v2 trigger handler, which explicitly rejects duplicate/in-flight request IDs with an `ErrConflict`/"in-flight request" error, see `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go` lines 317-355). The map key is not namespaced by sender/session, only by the raw attacker-supplied `MessageId`.

When a node later responds, `handleWebAPITriggerMessage` looks up and deletes by `MessageId` and invokes whatever callback is currently stored:
```
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ...})
``` [4](#0-3) 

Exploit flow: attacker A sends `web_api_trigger` with `MessageId="X"`, gets stored as `savedCallbacks["X"]=cbA`, and the request is fanned out to DON nodes. Before the DON responds, attacker/victim B sends another `web_api_trigger` with the same `MessageId="X"`, overwriting the map entry with `cbB`. When the DON node responds to A's original request (echoing `MessageId="X"` because nodes copy the incoming `MessageId` back into their response, see `sendResponse` in `core/capabilities/webapi/trigger/trigger.go` lines 308-321), the gateway invokes `cbB.SendResponse(...)` with A's response payload — delivering A's data (which can include `ErrorMessage`/`Body` fields reflecting details of A's original web request, e.g. via `Response`/`TriggerResponsePayload` in `core/services/gateway/handlers/capabilities/webapi.go` lines 17-48) to B. A's own callback (`cbA`) is orphaned and never invoked (silent loss, until the caller's own timeout).

No existing check (signature, allowlist, or the `Response.Validate()`/payload validation logic) prevents this, because those validations operate on payload content correctness, not on MessageId ownership or uniqueness. The code explicitly acknowledges missing allowlist/rate-limit enforcement at this call site: `// TODO: apply allowlist and rate-limiting here` [5](#0-4) .

### Impact Explanation
This is a genuine cross-user response confusion / information-disclosure bug: an unprivileged, unauthenticated-at-this-layer caller of the `web_api_trigger` gateway method can obtain the response payload intended for a different caller's request simply by reusing/colliding a `MessageId`. Depending on what a workflow's HTTP trigger response carries (which can include upstream metadata/error text), this leaks data belonging to another user's request. It also causes denial/loss of the legitimate first requester's response (their callback is silently dropped and only resolves via timeout). This matches the Chainlink bounty impact class of unauthorized disclosure of another caller's data / cross-user response confusion.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs the ability to send `web_api_trigger` requests to the gateway (any credentialed API caller who can reach this DON's endpoint, since the code comment shows allowlisting/rate-limiting is not yet applied at this call site). No node/operator/admin privilege is required. The attacker needs to win a narrow race (send colliding `MessageId` before the first request's node response arrives and before `pruneCallbacks` runs, which only prunes every `CallbackPruneIntervalSec`, default 30s, and callbacks live up to `CallbackMaxAgeSec`, default 120s) — this is a generous window, making the race easily repeatable and highly feasible for a scripted attacker.

### Recommendation
- Reject `HandleLegacyUserMessage` requests whose `MessageId` already has a live entry in `savedCallbacks` (mirror the v2 `httpTriggerHandler`'s "in-flight request"/`ErrConflict` behavior), returning an error to the second caller instead of overwriting.
- Namespace `savedCallbacks` keys by a value not fully controlled by the client and unique per connection/session (e.g., combine `MessageId` with sender/signer address, or generate the tracking key server-side and never accept the raw JSON-RPC `id` as the sole map key).
- Consider using an atomic `LoadOrStore`-style operation instead of the read-then-write pattern to close any TOCTOU window.

### Proof of Concept
Go handler-level integration test (add to `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Build two valid signed `triggerRequest` messages (`msgA`, `msgB`), each with a different signer but forcing the same `MessageId` (e.g., `"COLLIDE"`), both otherwise passing all `HandleLegacyUserMessage` validations (valid payload, fresh timestamp, correct method).
2. Create two separate callbacks `cbA := hc.NewCallback()`, `cbB := hc.NewCallback()`.
3. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)`, then before any node response is delivered, call `handler.HandleLegacyUserMessage(ctx, msgB, cbB)`.
4. Assert `handler.savedCallbacks["COLLIDE"]` now points to `cbB`'s wrapper (i.e., `cbA`'s entry was overwritten) — `require.NotEqual` on stored callback identity vs `cbA`.
5. Simulate the DON responding to `msgA`'s underlying request: build a node response `resp` via `hc.ValidatedResponseFromMessage(msgA)` and call `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
6. Assert that `cbB.Wait(ctx)` returns successfully with a payload equal to `codec.EncodeLegacyResponse(msgA)` (i.e., B received A's response data) — expected assertion: `require.Equal(t, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msgA), ErrorCode: api.NoError}, rB)`.
7. Assert that `cbA.Wait(ctx)` times out / never receives a response (data loss for A), confirming cross-user confusion rather than mere duplication.

### Citations

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/common/message_util.go (L46-57)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
