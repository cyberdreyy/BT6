### Title
Cross-user response leakage via user-controlled `MessageId` collisions in webAPI trigger callback map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores each incoming user request's callback keyed solely by the client-supplied `msg.Body.MessageId`, and `handleWebAPITriggerMessage` later looks up and delivers the response using only that same `MessageId`, with no binding to sender/session/DON member. Since `MessageId` is fully attacker-controlled (part of the client-signed message body, up to 128 bytes, with no server-enforced uniqueness), an unprivileged user can submit a request whose `MessageId` collides with a victim's in-flight request, causing the victim's saved callback to be overwritten and the eventual node response for that ID to be delivered to the attacker's callback instead.

### Finding Description
`api.MessageBody.MessageId` is chosen entirely by the requester and only validated for length/null-byte suffix in `Message.Validate()`; there is no gateway-side uniqueness enforcement across users/requests [1](#0-0) [2](#0-1) .

`HandleLegacyUserMessage` unconditionally writes into the shared map using this attacker-chosen key: `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`, with no check for an existing entry (no "insert if absent") [3](#0-2) . If a second (attacker) request arrives with the same `MessageId` as a victim's still-pending request, the attacker's callback silently replaces the victim's callback in the map. The original request is forwarded to all DON members verbatim, including the same `MessageId`, from both the victim's and the attacker's requests [4](#0-3) .

When a node responds, `handleWebAPITriggerMessage` looks up the callback purely by `msg.Body.MessageId` (with a sender check only against the node, not the original requester) and delivers whichever response arrives first to whatever is currently stored under that ID: [5](#0-4) . The node-message dispatch (`HandleNodeMessage`) only verifies `msg.Body.Sender == nodeAddr` (the DON node's own signature), not any binding to which end-user request triggered it [6](#0-5) . There is no requester/session identity stored in `savedCallback` — only `id` and `createdAt` [7](#0-6) .

Consequently, if a victim's trigger response for a colliding `MessageId` is delivered after the attacker's overwrite, it will be routed to the attacker's `Callback.SendResponse`, leaking the victim's webAPI trigger response body to the attacker. This does not require a malicious node or DON — an unprivileged gateway client choosing a colliding `MessageId` for their own legitimate-looking request is sufficient. This matches the audit's "race between two legitimate users sharing a colliding MessageId" and "attacker-influenceable MessageId generation" scenario, and does not depend on any node compromise (the node's signature check passes normally; the flaw is purely in gateway-side callback keying, in scope).

### Impact Explanation
Cross-user response leakage: an attacker can cause another user's webAPI trigger response (which may contain sensitive HTTP response bodies/headers from the workflow's target endpoint) to be delivered to the attacker's own connection/callback, and simultaneously deny the legitimate user their response (map entry deleted on first delivery). This falls under "unauthorized action on another user's job" / cross-user data exposure in the gateway's webAPI trigger path.

### Likelihood Explanation
Exploitability requires only: (1) the ability to submit signed gateway requests (already assumed attacker capability — "any address sending signed gateway requests"), and (2) choosing a `MessageId` value that collides with a targeted victim's in-flight request. Since `MessageId` has no server-side collision protection and is entirely client-chosen, an attacker who can predict, brute-force, or is informed of a victim's `MessageId` (e.g., sequential/short IDs used by some clients, or simply racing many concurrent requests with common ID patterns) can reliably trigger the overwrite within the request's TTL window (`defaultCallbackMaxAgeSec` = 120s), making repeated attempts practical.

### Recommendation
Bind saved callbacks to more than just `MessageId`: incorporate the requester's identity (e.g., signer/sender address extracted during `Validate()`) into the map key, or store the expected sender alongside the callback and verify it before overwriting an existing entry or delivering a response. Reject (or namespace) new registrations that would overwrite an existing non-expired `savedCallbacks` entry, and additionally validate that the response's `DonId`/originating context matches the stored request's context before invoking `SendResponse`.

### Proof of Concept
Go unit test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Create two mock `handlers.Callback` instances, `callbackA` (victim) and `callbackB` (attacker).
2. Call `handler.HandleLegacyUserMessage` with a victim message body whose `MessageId = "collide-1"`, signed by victim key, storing `callbackA`.
3. Before any node responds, call `handler.HandleLegacyUserMessage` again with an attacker-crafted message using the same `MessageId = "collide-1"`, signed by attacker key, storing `callbackB` — assert `h.savedCallbacks["collide-1"].Callback == callbackB` (overwrite occurred, no rejection).
4. Simulate a DON node response for `MessageId = "collide-1"` via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`, carrying the victim's real trigger result payload.
5. Assert that `callbackB.SendResponse` (attacker) is invoked with the victim's payload, and `callbackA.SendResponse` (victim) is never invoked — demonstrating cross-user response delivery confusion.

### Citations

**File:** core/services/gateway/api/message.go (L42-52)
```go
type MessageBody struct {
	MessageId string `json:"message_id"`
	Method    string `json:"method"`
	DonId     string `json:"don_id"`
	Receiver  string `json:"receiver"`
	// Service-specific payload, decoded inside the Handler.
	Payload json.RawMessage `json:"payload,omitempty"`

	// Fields only used locally for convenience. Not serialized.
	Sender string `json:"-"`
}
```

**File:** core/services/gateway/api/message.go (L54-68)
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
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L72-76)
```go
type savedCallback struct {
	id        string
	createdAt time.Time
	handlers.Callback
}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-267)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
	start := time.Now()
	switch msg.Body.Method {
	case MethodWebAPITrigger:
		err = h.handleWebAPITriggerMessage(ctx, msg, nodeAddr)
	case MethodWebAPITarget, MethodComputeAction, MethodWorkflowSyncer:
		err = h.handleWebAPIOutgoingMessage(ctx, msg, nodeAddr)
	default:
		err = fmt.Errorf("unsupported method: %s", msg.Body.Method)
	}
	h.metrics.recordHandleDuration(ctx, time.Since(start), msg.Body.Method, err == nil)
	return err
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-420)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
