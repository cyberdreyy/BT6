This confirms the vulnerability: the sibling `requestcache.go` implementation deliberately keys pending requests by a `globalId{sender, id}` tuple [1](#0-0) [2](#0-1) , precisely to scope callbacks per-sender — but `capabilities/handler.go` does not follow this pattern and keys solely by `msg.Body.MessageId`, which is fully attacker-supplied and only validated for length/format, not uniqueness or ownership [3](#0-2) .

### Title
Cross-user response confusion via unscoped MessageId in web API trigger callback map - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`handler.savedCallbacks` is keyed only by `msg.Body.MessageId`, a value fully controlled by the unprivileged client submitting the request, with no binding to sender address, DonId, or nodeAddr. Two different unprivileged users can submit `web_api_trigger` requests with the same `MessageId`, causing one user's callback registration to silently overwrite another's, or causing a stale/delayed node response for one user's request to be delivered to a different, unrelated user.

### Finding Description
`HandleLegacyUserMessage` registers a pending callback keyed by the raw, user-supplied `MessageId`: [4](#0-3) 

`Message.Validate()` only checks that `MessageId` is non-empty, ≤128 bytes, and doesn't end in a null byte — it never enforces uniqueness or binds the ID to the sender: [5](#0-4) 

When a DON node later replies with the same `MessageId` (which the gateway forwards unchanged to the node as the JSON-RPC request ID via `ValidatedRequestFromMessage`), `handleWebAPITriggerMessage` looks up and deletes the entry keyed solely by that ID, then delivers the response to whichever callback is currently stored there: [6](#0-5) 

There is no check that `msg.Body.Sender`, `nodeAddr`, or `DonId` of the incoming node response matches the sender/context of the original registrant. Contrast this with `common.requestCache`, used elsewhere in the same package tree, which explicitly composes the cache key from `{sender, id}` to prevent exactly this kind of collision: [7](#0-6) 

Exploit flow: User A submits a signed `web_api_trigger` message with `MessageId="M"`; it is registered under `savedCallbacks["M"]`. If a node response for A arrives late (e.g., after pruning/timeout or a duplicate/delayed message), and in the interim User B (an unrelated unprivileged client) submits their own signed request also using `MessageId="M"`, the map entry is silently overwritten with B's callback. The delayed response — encoding A's original external HTTP call result via `sendHTTPMessageToClient`/`Response` payload — is then delivered to B via `savedCb.SendResponse(...)`, since the lookup at line 150 cannot distinguish the two registrants.

### Impact Explanation
This is a cross-user response confusion: an unprivileged client can receive response data belonging to a different, unrelated caller's `web_api_trigger` request. Since the payload contains the body/headers/status code of an external HTTP call initiated on behalf of another user's workflow trigger, this constitutes disclosure of another user's data to an unauthorized party, matching the "cross-user response confusion" impact class called out in scope.

### Likelihood Explanation
Exploitability requires no special privilege — any two unauthenticated/unprivileged callers who can sign and submit `web_api_trigger` requests to the gateway can pick an identical `MessageId` string (attacker fully controls this field, only constrained by length ≤128 bytes and no trailing null). The race window depends on timing (one request's callback must still be, or become, associated with the same key when the other's node response arrives), which requires some timing coordination but is plausible given the 120-second default callback max age and up to 20,000 concurrent pending callbacks, and is trivially reproducible in a unit test that directly manipulates `h.savedCallbacks`.

### Recommendation
Scope `savedCallbacks` keys by both `msg.Body.Sender` (or another caller-identity value bound to the original signed request) and `MessageId`, mirroring the `globalId{sender, id}` pattern already used in `common/requestcache.go`. Alternatively, generate the `MessageId` used as the map key server-side (e.g., gateway-assigned UUID) rather than trusting the client-supplied value directly for callback correlation.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler `h` with a mocked `handlers.DON`.
2. Register two mock `handlers.Callback` implementations, `callbackA` and `callbackB`, each recording the `UserCallbackPayload` passed to `SendResponse`.
3. Directly insert `h.savedCallbacks["M"] = &savedCallback{Callback: callbackA, ...}` (simulating A's pending request).
4. Overwrite: `h.savedCallbacks["M"] = &savedCallback{Callback: callbackB, ...}` (simulating B's registration reusing the same MessageId, as would occur via a second `HandleLegacyUserMessage` call with the same `MessageId`).
5. Call `h.handleWebAPITriggerMessage(ctx, msg, nodeAddr)` with `msg.Body.MessageId = "M"` and a payload representing A's original HTTP response data.
6. Assert `callbackB.SendResponse` was invoked with A's response payload, and `callbackA.SendResponse` was never called — demonstrating cross-user delivery.

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L34-37)
```go
type globalId struct {
	sender string
	id     string
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-57)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
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
