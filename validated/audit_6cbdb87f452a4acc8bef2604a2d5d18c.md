### Title
Attacker-chosen `MessageId` collision in the WebAPI capabilities gateway handler enables cross-user response hijacking - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The gateway's legacy-message WebAPI trigger handler stores a pending caller callback in a shared map keyed purely by the client-supplied `MessageId` field, without checking that the ID is unique or already in use by another request. `MessageId` is fully attacker-controlled (bounded only by length/charset checks), so an unprivileged client can pick the same `MessageId` as another in-flight legitimate request and silently overwrite the stored callback, causing the DON's eventual response to be delivered to the wrong caller.

### Finding Description
`api.Message.Validate()` only checks that `MessageId` is non-empty, ≤128 bytes, and doesn't end in a null byte — it never enforces uniqueness or binds the ID to the requesting sender beyond being part of the signed payload: [1](#0-0) 

When an unprivileged client hits the gateway with a legacy user message (`HandleLegacyUserMessage`), the capabilities handler unconditionally writes into a shared `savedCallbacks` map using the attacker-controlled `MessageId` as the key, with no check for an existing entry: [2](#0-1) 

Later, when any DON node replies with `MethodWebAPITrigger` and the same `MessageId`, the handler looks up the map by that same user-supplied ID and forwards the *first* response received to whatever callback currently occupies that slot, then deletes the entry: [3](#0-2) 

This mirrors the root cause pattern in the reference report: a value supplied by an unprivileged actor (there, `lastProposalId`; here, `MessageId`) is used directly as the anchor/key for a shared resource (there, the auction-revenue window end; here, the pending-response slot) before any check ties that value to the legitimate, eligible originator. Because the map write is a plain overwrite (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) rather than an insert-if-absent, an attacker can race or predict a victim's `MessageId` and clobber the stored callback, so the eventual node response — potentially containing another user's HTTP trigger result/payload — is delivered to the attacker's callback instead of the victim's, and the victim never receives a response (or receives the attacker's stale one on the next call).

### Impact Explanation
This is a cross-user response confusion: an unprivileged, unauthenticated (for the legacy-message path) sender can hijack another concurrent caller's gateway response by colliding on `MessageId`. Depending on payload contents this can leak response data meant for another workflow/tenant to the attacker and/or cause denial of legitimate response delivery to the victim. No special privilege beyond the ability to submit gateway requests is required, matching the "unprivileged actor" and "cross-user response confusion" criteria.

### Likelihood Explanation
`MessageId` values are chosen entirely by the client and are not required to be random/high-entropy by the protocol (the validation logic imposes only length/format checks). If callers use predictable, short, or sequential IDs (or if an attacker can observe an ID in transit/logs before the node responds), collision is straightforward and requires only sending a second legacy message with the same ID while the original is still pending (within `CallbackMaxAgeSec`, default 120s). No cryptographic or economic barrier prevents this.

### Recommendation
Do not use a purely client-supplied string as the sole key into the shared `savedCallbacks` map. Either:
- Scope the map key by `(Sender, MessageId)` (the signer address is already derived during `Validate()` via `ExtractSigner`), rejecting/namespacing collisions across different senders, or
- Refuse to overwrite an existing, non-expired entry for the same `MessageId` and return an error instead, or
- Generate a gateway-side unique correlation ID for each accepted request instead of trusting the client-chosen `MessageId` for internal bookkeeping.

### Proof of Concept
1. Victim submits a legacy WebAPI trigger message with `MessageId = "X"`; gateway stores `savedCallbacks["X"] = victimCallback` and forwards the request to DON nodes.
2. Before the DON responds, attacker submits their own legacy WebAPI trigger message reusing `MessageId = "X"` (valid per `Validate()` since only length/format are checked); gateway overwrites `savedCallbacks["X"] = attackerCallback`.
3. When any DON node's response for `MessageId = "X"` (which could be the response meant for the victim's request, since nodes echo back the ID they were given) arrives at `handleWebAPITriggerMessage`, the handler looks up `savedCallbacks["X"]`, finds `attackerCallback`, and delivers the response to the attacker while the victim's request is silently dropped.

### Citations

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
