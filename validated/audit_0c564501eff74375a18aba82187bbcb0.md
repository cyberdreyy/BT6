### Title
Cross-user response confusion via unscoped MessageId in gateway capabilities handler's savedCallbacks map - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The `handler.HandleLegacyUserMessage` function in the Chainlink Gateway's `web-api-capabilities` handler stores a per-request callback keyed only by the client-supplied `MessageId`, with no check for whether that ID is already in use and no binding to the sender/signer. Because `MessageId` is fully attacker-controlled and travels through the public, internet-facing Gateway, an unprivileged user can submit a request that reuses another in-flight request's `MessageId`, silently overwriting the legitimate caller's saved callback. This is the same bug class as the ULTI report: a missing validation on an attacker-influenced value that should have been checked against staleness/uniqueness before being trusted, leading to state (here, response routing state) being clobbered/griefed.

### Finding Description
`Message.Validate()` only checks length bounds and null-byte suffixes on `MessageId`; it never enforces uniqueness or ties the ID to the sender: [1](#0-0) 

In `HandleLegacyUserMessage`, once a message passes validation and staleness checks, the handler unconditionally writes into the shared `savedCallbacks` map using only `msg.Body.MessageId` as the key — with no check for an existing, still-pending entry: [2](#0-1) 

When a DON node later responds, the response is matched back to a caller purely by `msg.Body.MessageId` via `handleWebAPITriggerMessage`, and the *first* saved callback found under that ID is the one that receives the payload: [3](#0-2) 

Because the map is keyed solely by `MessageId` (not `(Sender, MessageId)`), if two different senders' requests carry the same `MessageId` while both are in flight, the second write silently replaces the first entry in the map. The original caller's callback reference is lost/never invoked (it will only ever time out), while the attacker's callback — now occupying that map slot — receives whichever node response arrives keyed to that `MessageId`.

### Impact Explanation
This is the gateway analog of the LP-contribution griefing bug: an unauthenticated, client-supplied parameter (`MessageId`, analogous to the ULTI `deadline`) is not validated for a state that would make it unsafe to trust (uniqueness/ownership, analogous to "not already expired"), and the missing check causes another party's in-flight state to be silently clobbered. Concretely:
- The legitimate caller's request is griefed: their callback is orphaned and will only resolve via a timeout, denying them a legitimate response (a form of resource/availability griefing analogous to the LP funds being stuck).
- Depending on the workflow response contents, the attacker's callback could receive data intended for a different, unrelated request/session — a cross-user response confusion condition.

### Likelihood Explanation
Likelihood is Low-to-Medium and situational: the attacker must submit a request that races another party's request while it is still pending (bounded by `CallbackMaxAgeSec`, default 120s) and must guess/observe the same `MessageId`. Since messages transiting the Gateway are visible on the wire/logs and `MessageId` is not required to be a secret or cryptographically random value (many clients use simple/sequential/timestamp-derived IDs, as seen in the reference script that uses `"12345"` as a hardcoded example ID), this is plausible for predictable ID schemes but not trivial for well-randomized ones.

### Recommendation
- Scope the `savedCallbacks` key by both the signer/sender and `MessageId` (e.g., `sender + ":" + messageId`) rather than `MessageId` alone, since `Message.Validate()` already derives `Body.Sender` from the signature.
- Additionally, reject (rather than silently overwrite) a new save when an active, non-expired callback already exists for the same key, mirroring the ULTI recommendation of reverting rather than silently proceeding with an invalid/conflicting state.

### Proof of Concept
1. Victim signs and sends a `web_api_trigger` request with `MessageId = "X"`; the Gateway calls `HandleLegacyUserMessage`, which stores `savedCallbacks["X"] = victimCallback` at [4](#0-3) .
2. Before the DON responds, an attacker (any other holder of a valid signing key accepted by the DON/allowlist) sends their own signed request also using `MessageId = "X"`. `Message.Validate()` accepts it because `MessageId` uniqueness is never checked [5](#0-4) .
3. The handler overwrites `savedCallbacks["X"]` with the attacker's callback.
4. When a DON node responds with `MessageId = "X"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds the attacker's callback, deletes the entry, and delivers the response to the attacker instead of the victim [6](#0-5) . The victim's request silently never completes (times out).

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L410-420)
```go

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
