This confirms the vulnerability. The `capabilities` handler's `savedCallbacks` map at `core/services/gateway/handlers/capabilities/handler.go:411-412` stores callbacks keyed solely by `msg.Body.MessageId` with no existence check, unlike `vault/handler.go:466-472` and `confidentialrelay/handler.go:368-374`, which both explicitly reject duplicate IDs with `errors.New("request ID already exists: " + req.ID)`. Since `Message.Validate()` (`core/services/gateway/api/message.go:54-88`) only checks signature validity of the sender over their own payload — it does not bind `MessageId` uniqueness to a sender — any two distinct, independently-signed senders can pick the same `MessageId`.

### Title
Cross-user response confusion via MessageId collision in capabilities handler's savedCallbacks map - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The legacy WebAPI-trigger flow in `core/services/gateway/handlers/capabilities/handler.go` stores each in-flight HTTP request's callback in `h.savedCallbacks` keyed only by the client-supplied `msg.Body.MessageId`, with no uniqueness check. An unprivileged client can pick the same `MessageId` used by a concurrent victim request (message IDs are entirely attacker-chosen and only need a valid signature over the attacker's own payload) to overwrite the victim's saved callback, causing the DON's response addressed to the victim's request to be delivered to the attacker's HTTP connection instead.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes the raw JSON-RPC request via `JsonRPCCodec.DecodeJSONRequest`, which sets `msg.Body.MessageId = request.ID` directly from client input [1](#0-0) . For legacy requests, `msg.Validate()` is called, which validates the ECDSA signature over the message body but does not enforce any relationship between `MessageId` and sender identity, nor global uniqueness [2](#0-1) . The request is then routed to `HandleLegacyUserMessage` in the capabilities handler, which unconditionally overwrites the map entry:

```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [3](#0-2) 

There is no pre-check like `if _, found := h.savedCallbacks[msg.Body.MessageId]; found { return error }`. When the DON node later responds, `handleWebAPITriggerMessage` looks up and deletes the entry by `MessageId` and forwards the response to whichever callback is currently stored, with an explicit comment acknowledging only the first-arriving response is delivered and others are ignored: "Send first response from a node back to the user, ignore any other ones" [4](#0-3) .

This is in stark contrast to the two other request-response handlers in the same package family, `vault/handler.go` and `confidentialrelay/handler.go`, both of which explicitly reject duplicate IDs before insertion:
```go
if h.activeRequests[req.ID] != nil {
    h.lggr.Errorw("request id already exists", "requestID", req.ID)
    return nil, errors.New("request ID already exists: " + req.ID)
}
``` [5](#0-4) [6](#0-5) 

Exploit flow: (1) victim sends a legacy `webAPITrigger` request signed with the victim's key and `MessageId = "X"`; `h.savedCallbacks["X"]` is set to the victim's callback while `gateway.ProcessRequest` blocks on `callback.Wait(ctx)`. (2) attacker, using their own private key, signs an independent request also with `MessageId = "X"` before the DON responds to the victim's message; this passes `msg.Validate()` because it's a valid signature over the attacker's own data, and `HandleLegacyUserMessage` silently overwrites `h.savedCallbacks["X"]` with the attacker's callback. (3) When the DON node responds to the victim's original triggered message (echoing back `MessageId = "X"`), `handleWebAPITriggerMessage` looks up `h.savedCallbacks["X"]`, finds the attacker's callback, deletes the entry, and delivers the victim's response payload to the attacker's blocked HTTP request.

### Impact Explanation
This is a cross-user response confusion vulnerability: an unprivileged client can hijack another user's in-flight webAPITrigger response by colliding on the client-controlled `MessageId`. Depending on what data the DON returns for a triggered workflow, this could expose data intended for the victim to the attacker, matching Chainlink's "unauthorized access to another user's job/request data" impact class. This is more limited in scope than a secrets-disclosure vulnerability (the payload for this path is workflow trigger response data, not vault secrets, since vault/confidentialrelay handlers already reject duplicate IDs), but it does violate the "one request ID maps to exactly one authenticated sender" isolation invariant.

### Likelihood Explanation
Exploitability requires only the ability to send unauthenticated POST requests to the gateway with a valid self-signed legacy message (any ECDSA keypair the attacker controls) and a `MessageId` that collides with the timing window of a legitimate in-flight request. No special role, credential, or knowledge of the victim's private key is required — only knowledge/guessing of the victim's chosen `MessageId` string and winning a race before the DON responds. Message IDs are often short and may be predictable (e.g., counters, workflow run identifiers) in some client integrations, and the race window is the full round-trip time to the DON, which can be substantial (seconds), making this practically repeatable.

### Recommendation
Add a duplicate-ID rejection check in `HandleLegacyUserMessage` before inserting into `h.savedCallbacks`, mirroring the pattern already used in `vault/handler.go` and `confidentialrelay/handler.go`:
```go
h.mu.Lock()
if _, found := h.savedCallbacks[msg.Body.MessageId]; found {
    h.mu.Unlock()
    return callback.SendResponse(handlers.UserCallbackPayload{
        RawResponse: codec.EncodeNewErrorResponse(msg.Body.MessageId, api.ToJSONRPCErrorCode(api.UserMessageParseError), "duplicate message ID in-flight", nil),
        ErrorCode: api.UserMessageParseError,
    })
}
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
Additionally, consider binding responses to the original sender by validating that the DON's response `Sender`/`Receiver` matches the original requester before forwarding it back.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Set up the capabilities `handler` as in existing tests (`setupHandler(t)`).
2. Craft `victimMsg` signed with `victimKey`, `MessageId = "dup-id"`, call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` — assert `handler.savedCallbacks["dup-id"]` is the victim's callback.
3. Before delivering any node response, craft `attackerMsg` signed with a different `attackerKey`, same `MessageId = "dup-id"`, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)`.
4. Current (vulnerable) behavior: assert `handler.savedCallbacks["dup-id"]` now equals the attacker's callback, silently discarding the victim's registration — proving overwrite instead of `ConflictError`.
5. Simulate the DON responding to the victim's original message id via `handler.handleWebAPITriggerMessage(ctx, nodeResponseMsg /* MessageId: "dup-id" */, nodeAddr)` and assert that `attackerCallback.Wait(ctx)` (not `victimCallback.Wait(ctx)`) receives the response — demonstrating cross-user response delivery.
6. After the fix, step 3 should instead return an error/`ConflictError` to the attacker and leave `handler.savedCallbacks["dup-id"]` pointing to the victim's callback.

### Citations

**File:** core/services/gateway/api/jsonrpccodec.go (L24-33)
```go
func (*JsonRPCCodec) DecodeJSONRequest(request jsonrpc2.Request[json.RawMessage]) (*Message, error) {
	var msg Message
	err := json.Unmarshal(*request.Params, &msg)
	if err != nil {
		return nil, err
	}
	msg.Body.MessageId = request.ID
	msg.Body.Method = request.Method
	return &msg, nil
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

**File:** core/services/gateway/handlers/vault/handler.go (L466-472)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-374)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
```
