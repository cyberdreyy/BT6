### Title
Cross-user response hijacking via MessageId collision in `HandleLegacyUserMessage` savedCallbacks map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each user's response `Callback` keyed only by the attacker/user-controlled `msg.Body.MessageId`, with no check that the ID is unique or bound to the request's signer. Any client able to send a `MethodWebAPITrigger` message can choose an arbitrary `MessageId`, and if it collides with another in-flight request, it silently overwrites `h.savedCallbacks[MessageId]`. When the DON later responds with that same ID, `handleWebAPITriggerMessage` delivers the payload to whichever callback is currently stored — potentially the attacker's — not necessarily the original requester's.

### Finding Description
`MessageId` is fully attacker-controlled: `Message.Validate()` (`core/services/gateway/api/message.go:54-88`) only checks length/format and derives `Sender` from the signature, but never ties `MessageId` uniqueness to a sender. Anyone (any keypair) can sign a well-formed `api.Message` with any `MessageId` they choose and submit it as a `MethodWebAPITrigger` legacy request.

In `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`), after basic validation the handler does:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [1](#0-0) 
There is no check for an existing entry under the same key, no binding of the key to `msg.Body.Sender`, and no rejection on collision — the map entry (and thus the `Callback`, i.e. the HTTP response channel back to whichever client is waiting) is unconditionally overwritten.

Later, when any DON node replies with that `MessageId`, `handleWebAPITriggerMessage` looks the ID up, deletes it, and forwards the *first* response received to whatever `savedCallback` is currently stored:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
if found {
    return savedCb.SendResponse(...)
}
``` [2](#0-1) 

Exploit flow:
1. Victim sends a legitimate `web_api_trigger` request with `MessageId = "X"`; gateway stores victim's `Callback` under `savedCallbacks["X"]` and forwards the request to all DON members.
2. Before the DON responds, attacker sends their own signed `web_api_trigger` message also using `MessageId = "X"` (attacker controls this value freely since it's just part of the payload they sign). This overwrites `savedCallbacks["X"]` with the attacker's `Callback`.
3. When a DON node responds to message ID "X" (which could be the answer to either the victim's or attacker's forwarded request, since both were sent to the DON with the same ID), `handleWebAPITriggerMessage` delivers that response to whichever `Callback` is currently stored — potentially the attacker's — while the victim's original HTTP call either hangs/times out or is dropped/overwritten, and the attacker receives the response destined for/derived from the victim's flow.

No existing mechanism (auth middleware, sender check, or explicit ID-collision check) prevents this, since `MethodWebAPITrigger` is a legacy trigger endpoint with no allowlist/rate-limit applied yet (explicitly noted as a TODO at line 384: `// TODO: apply allowlist and rate-limiting here`) [3](#0-2) , and `HandleNodeMessage`/`handleWebAPITriggerMessage` only validate that the DON node's `Sender` matches the connection's `nodeAddr` [4](#0-3) , not that the original requester of a given `MessageId` is unique.

### Impact Explanation
This is a cross-user response confusion / request impersonation vulnerability: an unauthenticated attacker can hijack the capability-execution response intended for another user by colliding on `MessageId`, or cause the victim's callback to be silently dropped (denial of response to victim). This matches the "cross-user response confusion" / "unauthorized action on another user's request" impact class called out in the audit scope.

### Likelihood Explanation
No credential is required beyond the ability to POST a `MethodWebAPITrigger` message to the gateway (attacker can generate their own keypair and sign a message with a chosen `MessageId`). The attack requires only timing a second request with the duplicate ID before the first completes (feasible given typical DON round-trip latency and configurable `defaultCallbackMaxAgeSec` = 120s window), making it readily repeatable.

### Recommendation
Bind `MessageId` uniqueness to the original request context: reject/`SendResponse` an error immediately if `h.savedCallbacks[msg.Body.MessageId]` already exists rather than overwriting it, and/or scope the map key to `(Sender, MessageId)` so a different signer cannot collide with another user's in-flight ID. Additionally, verify on `handleWebAPITriggerMessage` that the responding node's message correlates to a request actually sent for that specific saved callback (e.g., by storing/verifying the expected sender or payload hash alongside the callback).

### Proof of Concept
Add a table/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Call `handler.HandleLegacyUserMessage` with a valid signed trigger message `msgA` using `MessageId = "X"` and callback `cbA`; assert `handler.savedCallbacks["X"].Callback == cbA`.
2. Before resolving, call `handler.HandleLegacyUserMessage` again with a different signer's signed trigger message `msgB`, also using `MessageId = "X"`, callback `cbB`; assert no error is returned and `handler.savedCallbacks["X"].Callback` is now `cbB` (overwritten), proving no sender/ID uniqueness check exists.
3. Simulate a DON node response for `MessageId = "X"` via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`; assert the response is delivered to `cbB.Wait(...)` instead of `cbA`, demonstrating the victim (`cbA`) never receives a response (or receives none) while the attacker (`cbB`) receives the routed payload.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
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
