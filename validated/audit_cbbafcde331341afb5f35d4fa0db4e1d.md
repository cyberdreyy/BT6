Confirmed: `Message.Validate()` only checks length/format constraints on `MessageId` [1](#0-0)  — it does not enforce uniqueness or bind the ID to a particular sender for authorization purposes. The `MessageId` is fully client-chosen (see `invoke_trigger.go` where `*messageID` flag is attacker-supplied, default `"12345"`) [2](#0-1) , and `HandleLegacyUserMessage` stores the callback keyed only by that value without checking for an existing/colliding entry [3](#0-2) .

### Title
Cross-user WebAPI trigger response confusion via attacker-controlled/colliding `MessageId` in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handleWebAPITriggerMessage` looks up the callback to deliver a node's trigger response solely by `msg.Body.MessageId` in the shared `h.savedCallbacks` map, and `HandleLegacyUserMessage` stores callbacks into that same map keyed only by the client-supplied `MessageId`, with no uniqueness check or binding to the requester's identity/session. Because `MessageId` is chosen entirely by the calling client (any unauthenticated/unprivileged gateway caller), an attacker can submit a trigger request using the same `MessageId` as a victim's in-flight request, overwrite the victim's stored callback, and receive the victim's node response instead.

### Finding Description
The relevant code path:
1. A client (attacker or victim) POSTs a legacy gateway message to the WebAPI trigger endpoint, which is routed to `HandleLegacyUserMessage`. This function validates payload/timestamp/method but never checks whether `msg.Body.MessageId` is already in use, then does:
   `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}` [3](#0-2) , then forwards the request to all DON members.
2. `msg.Body.MessageId` is entirely attacker-controlled — the message's `Validate()` only checks length/format, not uniqueness, and the signing/signature only proves who signed the request, not that the `MessageId` is unique or reserved for that sender [1](#0-0) .
3. When a DON node responds (echoing the same `MessageId` it received), `handleWebAPITriggerMessage` looks the ID up in `h.savedCallbacks`, deletes the entry, and delivers the response to whichever callback is currently stored under that key: `savedCb, found := h.savedCallbacks[msg.Body.MessageId]` ... `return savedCb.SendResponse(...)` [4](#0-3) .
4. If an attacker races or predicts the victim's `MessageId` (e.g., a fixed/sequential/timestamp-derived ID used by a client, or simply reusing a known ID such as the sample script's default `"12345"`) and submits their own trigger request with the same `MessageId` after the victim's request is stored but before the victim's node response arrives, the attacker's `HandleLegacyUserMessage` call overwrites `h.savedCallbacks[MessageId]` with the attacker's own callback. When the node's response for the *victim's original request* arrives (still carrying the original `MessageId`, since the gateway reuses the ID it forwarded to nodes), it is matched against the map entry — now the attacker's callback — and the victim's response payload (trigger event data derived from job/workflow payload) is delivered to the attacker via `savedCb.SendResponse`.
5. No authentication/authorization/session binding ties a `savedCallbacks` entry to the sender who created it; the map key space is shared globally across all callers of the handler for a given DON.

### Impact Explanation
This is a cross-user response confusion: an unprivileged/unauthenticated caller of the gateway's legacy WebAPI trigger endpoint can, by colliding a `MessageId`, redirect another user's trigger response to themselves, and correspondingly cause the victim's request to potentially receive the attacker's (or no) response, or cause their own crafted `MessageId` to be treated as first responder for a colliding ID. This corresponds to Chainlink bounty impact class "unauthorized access to another user's job/response data" / request-response confusion, since trigger response payloads can carry job/workflow-derived data intended only for the original requester's callback.

### Likelihood Explanation
Exploitability requires: (1) the attacker be an unprivileged caller able to send arbitrary signed legacy gateway messages (any external caller who can construct and sign a message, which is the expected access level of this public/legacy endpoint), and (2) knowledge or prediction of a `MessageId` in use by another party, plus a timing race between the victim's request being stored and its response arriving from the DON. Because `MessageId` is entirely client-chosen with no server-side uniqueness enforcement, and some client tooling in the repo uses static/predictable default IDs (e.g., `"12345"`), collisions are plausible in environments with low-entropy or predictable ID generation, or if a malicious client intentionally guesses/brute-forces IDs against known/observed traffic patterns. The race window is bounded by node response latency (up to `CallbackMaxAgeSec`, default 120s) [5](#0-4) , which is a comparatively large window for practical exploitation.

### Recommendation
Bind `savedCallbacks` entries to the requester's identity (e.g., composite key of `Sender` address + `MessageId`, or a server-generated unguessable/opaque correlation ID independent of client input) and reject/enforce uniqueness when a `MessageId` is already active in `h.savedCallbacks` before overwriting it in `HandleLegacyUserMessage`.

### Proof of Concept
Go handler-level integration test plan:
1. Set up the handler as in `handler_test.go`'s `setupHandler`.
2. Victim: call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` with `MessageId = "shared-id"`, signed by victim's key; assert `SendToNode` called for DON members.
3. Attacker: before the node responds, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` using the same `MessageId = "shared-id"` but signed by attacker's key; assert this succeeds and overwrites the map entry (no error returned, no uniqueness check).
4. Simulate node response to the *victim's* original message (same `MessageId`, same payload) via `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
5. Assert that `attackerCallback.Wait(ctx)` receives the response (`UserCallbackPayload` containing the victim's node response), while `victimCallback.Wait(ctx)` never receives a response (times out) — demonstrating cross-user response delivery confusion.

### Citations

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

**File:** core/scripts/gateway/web_api_trigger/invoke_trigger.go (L98-105)
```go
	msg := &api.Message{
		Body: api.MessageBody{
			MessageId: *messageID,
			Method:    *methodName,
			DonId:     *donID,
			Payload:   payloadJSON,
		},
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
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
