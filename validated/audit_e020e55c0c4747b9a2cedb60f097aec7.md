### Title
Attacker-chosen `msg.Body.MessageId` collision in `savedCallbacks` map allows response hijacking between unrelated legacy trigger requests - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` keys the `savedCallbacks` map purely by the client-supplied `msg.Body.MessageId`, with no binding to the sender/signer. Because `MessageId` is attacker-chosen (it is signed by the attacker's own key but its value is arbitrary text under the attacker's control), an attacker can pick an ID that collides with another still-in-flight request and overwrite `h.savedCallbacks[id]`, redirecting that ID's eventual node response to the attacker's own callback.

### Finding Description
`HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`) stores the caller's `Callback` under the raw message ID with no collision check: [1](#0-0) 
This unconditionally overwrites any previous entry (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) if one exists for the same ID, without checking `found` or comparing the request's `Sender`.

When a DON node later responds with `MethodWebAPITrigger`, the gateway looks the callback up solely by `MessageId` and delivers the response to whoever is currently stored there, then deletes the entry: [2](#0-1) 

`msg.Body.MessageId` is not a gateway-generated nonce; it comes directly from the request the caller supplies (via `ValidatedMessageFromReq`, which sets `m.Body.MessageId = req.ID` from the client-supplied JSON-RPC `id`): [3](#0-2) 
The signature only proves the message came from the stated signer; it does not prove the `MessageId` is unique or bound to that particular request session. Any signer (attacker A, using their own valid key) can construct and sign a message whose `MessageId` equals the ID currently used by victim B's in-flight request.

Exploit flow:
1. Victim B sends a legitimate `HandleLegacyUserMessage` request with `MessageId = X`; the gateway stores `savedCallbacks[X] = B's callback` and forwards to DON nodes.
2. Before any node responds, attacker A sends a second `HandleLegacyUserMessage` request (signed with A's own key) whose `Body.MessageId` is also `X`. This overwrites `savedCallbacks[X]` with A's callback. A's request is separately forwarded to the DON as well.
3. When a node responds to the *original* request (ID `X`), `handleWebAPITriggerMessage` looks up `savedCallbacks[X]`, finds A's callback (having overwritten B's), and delivers B's response payload to A instead of B — and B never receives its response (blocked indefinitely / times out).

No part of the validation chain (`Message.Validate`, `ValidatedRequestFromMessage`, or `HandleLegacyUserMessage`'s own checks for payload decoding, timestamp staleness, method name) checks for ID uniqueness or binds the stored callback to the sender.

### Impact Explanation
This is a cross-user response confusion / request hijacking bug: an unprivileged, unauthenticated (from the node's perspective) but signed gateway client can steal or intercept another caller's trigger-request response, or deny that caller's response delivery (denial of service against a specific victim request). Depending on what data the legacy trigger response carries, this can leak the content of another user's request/response cycle to the attacker. This matches the "request impersonation / cross-user response confusion" impact class called out in scope.

### Likelihood Explanation
Exploitability requires: (1) attacker can reach the gateway's legacy user-message endpoint and sign messages with their own arbitrary private key (freely available, no special privilege), and (2) attacker can guess or observe another in-flight request's `MessageId` and win the race to send a colliding ID before that request completes (the callback window is bounded by `CallbackMaxAgeSec`, default 120s, giving a generous race window). If `MessageId`s are predictable/sequential or observable (e.g., via client-controlled IDs, shared workflows, or brute-forcing short IDs), likelihood is significant; if IDs are high-entropy random UUIDs generated independently per legitimate client, the attacker still only needs to guess/collide with one ID within the TTL window, which is a probabilistic but non-trivial attack absent hard confirmation of ID entropy guarantees enforced by this code path (the code itself imposes no minimum entropy or uniqueness requirement on `MessageId`, only a max length check in `Message.Validate`).

### Recommendation
Bind the saved-callback entry to the sender identity in addition to the message ID (e.g., key by `sender+messageId`, or store the expected sender and validate it on the response path), and reject/refuse to overwrite an existing `savedCallbacks` entry for a still-active ID (`found` check) rather than silently clobbering it. Additionally, consider having the gateway generate its own unique internal correlation ID rather than trusting a fully attacker-controlled `MessageId` for callback routing.

### Proof of Concept
Go handler-level test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Set up `handler` with a DON of ≥1 member and a rate limiter/config as in `setupHandler`.
2. Create two distinct signers (`keyB`, `keyA`), simulating victim B and attacker A.
3. Build `msgB := triggerRequest(t, keyB, ...)` and `msgA := triggerRequest(t, keyA, ...)` both with the *same* `MessageId = "12345"` (as already done for the single-caller case, but sign with two different keys).
4. Call `handler.HandleLegacyUserMessage(ctx, msgB, cbB)` then, before any node response arrives, call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)`.
5. Assert `handler.savedCallbacks["12345"].Callback == cbA` (i.e., B's callback was silently overwritten) — demonstrating the collision.
6. Simulate a DON node response for ID `"12345"` (`handler.HandleNodeMessage(ctx, resp, nodeAddr)`); assert that `cbA.Wait(ctx)` receives the response while `cbB.Wait(ctx)` times out/never receives a response — proving cross-user response delivery instead of isolated per-caller delivery.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/message_util.go (L46-52)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
```
