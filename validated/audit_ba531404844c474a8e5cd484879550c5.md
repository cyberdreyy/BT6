### Title
Cross-user response confusion via `MessageId` collision in gateway capabilities handler `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.savedCallbacks` map is keyed only by the caller-supplied `msg.Body.MessageId`, which is not scoped by sender/signer. Any signed `web_api_trigger` request from any address can choose a `MessageId` matching a victim's concurrently in-flight `MessageId`, overwriting the victim's saved callback so that a node's response can be routed to the attacker's connection or vice versa.

### Finding Description
`HandleLegacyUserMessage` stores the caller's `Callback` in `h.savedCallbacks[msg.Body.MessageId]` unconditionally, with no check for a pre-existing entry and no per-sender namespacing: [1](#0-0) 

`MessageId` validation only enforces length/printable/null-byte constraints (`api.Message.Validate`), not uniqueness or sender-binding: [2](#0-1) 

The code explicitly documents that allowlist/rate-limiting enforcement for `MethodWebAPITrigger` is not yet implemented (`// TODO: apply allowlist and rate-limiting here`), so any address able to produce a validly-signed message (which only requires generating an ECDSA keypair, not any pre-registered/whitelisted identity) can reach `HandleLegacyUserMessage`: [3](#0-2) 

When a node later responds with `MethodWebAPITrigger`, `handleWebAPITriggerMessage` looks up and deletes the callback purely by `MessageId` and delivers the node's response to whatever `Callback` is currently stored there: [4](#0-3) 

Exploit flow:
1. Victim sends a signed `web_api_trigger` request with `MessageId = "X"`; the gateway stores `savedCallbacks["X"] = victimCallback` and forwards the request to all DON members.
2. Before the DON responds, attacker (any unprivileged address, since no allowlist gate exists yet) sends their own signed `web_api_trigger` request also using `MessageId = "X"`. `HandleLegacyUserMessage` overwrites `savedCallbacks["X"]` with `attackerCallback`, and also forwards attacker's request to the DON.
3. Whichever node response (victim's or attacker's job) arrives first at `handleWebAPITriggerMessage` for `MessageId = "X"` is delivered to whatever callback is currently stored — i.e., the attacker can receive the victim's trigger response, or force the victim to receive the attacker's response, depending on race timing.

This is a real cross-user response confusion bug: the map has no sender binding and no collision protection, and per the explicit TODO comment, the layer intended to gate this (allowlist) is not implemented in this handler.

### Impact Explanation
This allows one unprivileged/unauthenticated-in-effect user to intercept another user's `web_api_trigger` response data (which may include workflow-specific payloads intended only for the legitimate requester) or to have their own malicious response silently substituted into another user's pending request, causing incorrect data to be returned to that victim. This matches the Chainlink bounty impact class of "cross-user response confusion" / unauthorized access to another user's request/response data.

### Likelihood Explanation
Requires only the ability to send arbitrary signed `HandleLegacyUserMessage`/`web_api_trigger` gateway messages (a valid ECDSA keypair, not a privileged credential) and to guess/observe a victim's in-flight `MessageId` and win a timing race against the DON's real response. Since allowlist/rate-limiting for this method is explicitly not yet enforced (per the TODO), the barrier to reaching `HandleLegacyUserMessage` is low; the remaining difficulty is purely timing (a race between two concurrent requests using the same `MessageId`), which is feasible and repeatable in a stress-test scenario, though it requires the attacker to know or predict the victim's `MessageId` value, which in practice may be generated with sufficient entropy by legitimate clients — this reduces real-world likelihood somewhat, but the underlying missing collision protection is a genuine code-level flaw independent of ID entropy.

### Recommendation
Scope `savedCallbacks` keys by both `MessageId` and the caller's authenticated `Sender`/session (e.g., key on `(sender, MessageId)` or reject/overwrite-protect duplicate `MessageId`s from different senders), and reject a new `HandleLegacyUserMessage` call whose `MessageId` already has a live, unexpired entry from a different sender. Additionally, implement the referenced allowlist/rate-limiting check before accepting `MethodWebAPITrigger` requests.

### Proof of Concept
Go handler-level test plan (extends `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct handler with a fake `handlers.DON` that records forwarded requests.
2. Create `victimCallback` and `attackerCallback` mocks implementing `handlers.Callback` with channels to capture `SendResponse` calls.
3. Call `h.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` where `victimMsg.Body.MessageId = "collide-id"`, signed by victim's key.
4. Before simulating the DON's response, call `h.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` with the same `MessageId = "collide-id"`, signed by attacker's key.
5. Assert `h.savedCallbacks["collide-id"].Callback == attackerCallback` (overwritten), proving the victim's callback was silently replaced.
6. Simulate a node response for `MessageId = "collide-id"` via `h.HandleNodeMessage`; assert that `attackerCallback.SendResponse` is invoked instead of `victimCallback.SendResponse`, confirming cross-user response delivery.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
```go
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L54-66)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```
