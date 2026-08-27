### Title
Missing existence check on `savedCallbacks` map allows response hijacking via MessageId collision - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` unconditionally overwrites `h.savedCallbacks[msg.Body.MessageId]` without checking whether an entry already exists for that key. Since `MessageId` is fully attacker-controlled (chosen by the client and only bound by their own signature), an attacker who can predict or observe a victim's in-flight `MessageId`/`DonId` pair can submit a second signed message with the same ID before the DON responds, silently discarding the victim's callback and causing the eventual DON response to be delivered to the attacker instead.

### Finding Description
`MessageId` is part of `api.MessageBody` and is chosen by the sender; it is only covered by the sender's own signature over `GetRawMessageBody` [1](#0-0) , so there is no cryptographic binding between a `MessageId` and any particular caller — any signer can pick any `MessageId` string of their choosing (subject only to the length/format checks in `Validate`) [2](#0-1) .

In `HandleLegacyUserMessage`, after validating the incoming message, the handler stores the caller's `Callback` keyed purely by `msg.Body.MessageId`, with no check for an existing entry: [3](#0-2) 

The corresponding read path, `handleWebAPITriggerMessage`, looks up and deletes the callback strictly by `MessageId` and routes the DON's first response to whichever `Callback` is currently stored under that key, with no sender/receiver validation tying the response back to the original requester: [4](#0-3) 

Exploit flow:
1. Victim submits a legitimate `MethodWebAPITrigger` message with `MessageId = X` to `DonId = D`. The handler stores `savedCallbacks[X] = victimCallback` and forwards the request to all DON members [5](#0-4) .
2. Before any DON node responds, the attacker (any unprivileged signer capable of sending a gateway message) submits their own signed message with the same `MessageId = X` and the same `DonId = D`. Because line 412 performs an unconditional map assignment, `savedCallbacks[X]` is overwritten with `attackerCallback`, silently discarding `victimCallback`.
3. When the DON responds for `MessageId = X`, `handleWebAPITriggerMessage` looks up `savedCallbacks[X]`, finds the attacker's callback, and delivers the (victim-originated) DON response to the attacker.

No existing check in the reachable code path (message signature verification, `Validate()`, rate limiting, or the callback map write) prevents this, since signature verification only proves who signed the *current* message, not that the `MessageId` is unique or reserved by the original requester.

### Impact Explanation
This is a cross-user response confusion vulnerability: an unprivileged attacker can hijack the gateway response intended for a different caller's in-flight web API trigger request, potentially exposing response data (e.g., HTTP response bodies/results from workflow triggers) that were meant only for the victim, and simultaneously causing denial-of-service for the victim's request (their callback never fires and the request appears to hang until pruning).

### Likelihood Explanation
Exploitability depends entirely on the attacker's ability to predict or observe a victim's `MessageId` and `DonId` before the DON responds. If callers generate `MessageId` values non-randomly (e.g., sequential counters, timestamps, or deterministic identifiers derived from job/workflow metadata) this is straightforward; if callers use cryptographically random, unguessable IDs, the race is infeasible. The code itself provides no defense-in-depth (no existence check), so the security relies entirely on client-side ID randomness/uniqueness, which is an unenforced assumption. No special privileges are needed beyond the ability to send a signed gateway message — a capability any external user with a valid signing key already has.

### Recommendation
In `HandleLegacyUserMessage`, check for and reject (or otherwise safely handle) a pre-existing `savedCallbacks[msg.Body.MessageId]` entry before storing the new callback, e.g., return an error response (such as a "duplicate message ID" error) instead of overwriting. Additionally, consider scoping the map key by `(Sender, DonId, MessageId)` rather than `MessageId` alone, so a different sender can never collide with another sender's in-flight request regardless of ID predictability.

### Proof of Concept
Go handler-level test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct two distinct `handlers.Callback` mocks: `victimCallback` and `attackerCallback`, each recording whatever `UserCallbackPayload` they receive via `SendResponse`.
2. Build a legitimate `api.Message` signed by victim's private key with `MessageId = "X"`, `DonId = <don>`, method `web_api_trigger`, valid payload/timestamp; call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` and assert `h.savedCallbacks["X"]` is set to the victim's callback.
3. Build a second `api.Message` signed by an attacker's private key, same `MessageId = "X"`, same `DonId`; call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)`.
4. Assert that this second call either fails/returns an error (desired fixed behavior) rather than succeeding, and that `h.savedCallbacks["X"].Callback` still refers to `victimCallback` (current buggy behavior shows it now equals `attackerCallback`).
5. Simulate a DON response for `MessageId = "X"` via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage` and assert `victimCallback.SendResponse` is invoked (expected) versus the current behavior where `attackerCallback.SendResponse` is invoked instead, proving hijacking.

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

**File:** core/services/gateway/api/message.go (L54-88)
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
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
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
