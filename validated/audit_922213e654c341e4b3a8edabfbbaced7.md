### Title
Cross-user response hijack via MessageId collision in `handler.handleWebAPITriggerMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores a caller's callback keyed only by the client-supplied `MessageId` with no uniqueness check, and `handler.handleWebAPITriggerMessage` looks up/deletes that callback by `MessageId` alone with no sender binding. Because `MessageId` is fully attacker-chosen (each requester signs their own message and picks their own `MessageId`), an unprivileged second user can submit a request using the same `MessageId` as an in-flight victim request, silently overwrite the map entry, and receive the DON node's response intended for the victim.

### Finding Description
The `MessageId` for a legacy gateway request is taken directly from the incoming JSON-RPC request ID with no server-side generation or randomness: `m.Body.MessageId = req.ID` in `ValidatedMessageFromReq` [1](#0-0) . `Message.Validate()` verifies only signature integrity/format and derives `Sender` from the signature over `MessageId`+`Method`+`DonId`+`Receiver`+`Payload` — it never checks that `MessageId` is unique or bound to a particular sender [2](#0-1) . Any signer can pick an arbitrary `MessageId` for their own signed message.

When a request arrives, `HandleLegacyUserMessage` unconditionally stores the callback:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [3](#0-2) 
with no pre-existence check — unlike the newer v2 handler, which explicitly rejects collisions:
```go
if _, found := h.callbacks[requestID]; found {
    h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, ...)
    return nil, fmt.Errorf("in-flight request ID: %s", requestID)
}
``` [4](#0-3) 

When the DON node eventually responds, `handleWebAPITriggerMessage` looks the callback up and deletes it purely by `MessageId`:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
``` [5](#0-4) 
There is no check that the response's payload/execution belongs to the same sender who registered the callback. The identical unguarded pattern exists in `dummyHandler` as well [6](#0-5) .

Exploit flow: victim sends a signed legacy request with `MessageId = X` to a given `DonId`; gateway stores `savedCallbacks[X] = victimCallback` and forwards to DON members. Before the DON responds, attacker (any other unprivileged signer) sends their own signed legacy request to the same `DonId` also using `MessageId = X` (fully under attacker's control, no requirement to know the victim's key — only the string ID must collide). This overwrites `savedCallbacks[X] = attackerCallback`. When the legitimate node eventually replies with `MessageId = X`, `handleWebAPITriggerMessage` delivers the victim's real webAPI-trigger response payload to `attackerCallback`, hijacking cross-user response data. The attacker's own original request is left uncompleted/timed out, but that is a self-inflicted DoS on their own request and doesn't prevent the hijack of the victim's response.

### Impact Explanation
This is a cross-user response/data confusion: an unprivileged gateway client can cause another user's node/DON response (including potentially sensitive trigger response payloads) to be delivered to the attacker's own HTTP callback instead of the legitimate requester, violating request/response binding. This matches the "cross-user response confusion" impact class called out in the audit scope, and can lead to disclosure of another user's workflow trigger response contents to an unauthorized party.

### Likelihood Explanation
Exploitability requires only: (1) knowledge or a guess of a victim's in-flight `MessageId` (client-controlled string, often predictable if apps use sequential/timestamp IDs, and in the worst case the attacker can also simply race by re-using IDs they've observed from prior interactions or public logs), and (2) submitting a normally-authorized signed request to the same public gateway endpoint before the DON responds — a narrow but realistic race window given asynchronous DON round-trips. No elevated privileges are required; both requester and attacker use standard signed gateway/API-token access, matching the "unprivileged attacker" threat model in scope.

### Recommendation
Bind the saved callback to the requester's identity and DON, not merely `MessageId`. At minimum: (1) in `HandleLegacyUserMessage`, reject (or namespace) the write if `h.savedCallbacks[msg.Body.MessageId]` already exists, mirroring the `ErrConflict` behavior implemented in `v2/http_trigger_handler.go`'s `setupCallback`; (2) key `savedCallbacks` by a composite of `(DonId/handler-scope, Sender, MessageId)` or generate `MessageId`s server-side to guarantee uniqueness/unpredictability; (3) verify on response delivery that the response's derived sender/context matches the originally saved request context before dispatching to the callback.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler `h` via `NewHandler` with a mock `DON`.
2. Build victim message `msgV` with `MessageId = "shared-id"` signed by victim key; call `h.HandleLegacyUserMessage(ctx, msgV, victimCallback)`. Assert `h.savedCallbacks["shared-id"].Callback == victimCallback`.
3. Build attacker message `msgA` with the same `MessageId = "shared-id"` signed by a different attacker key; call `h.HandleLegacyUserMessage(ctx, msgA, attackerCallback)`. Assert `h.savedCallbacks["shared-id"].Callback` has been silently replaced with `attackerCallback` (no error returned, no conflict).
4. Simulate the DON node response for `MessageId = "shared-id"` (containing victim-scoped payload) via `h.HandleNodeMessage(ctx, resp, nodeAddr)` → `handleWebAPITriggerMessage`.
5. Assert `attackerCallback.SendResponse` was invoked with the victim's response payload, and `victimCallback.SendResponse` was never called — demonstrating the response hijack.

### Citations

**File:** core/services/gateway/handlers/common/message_util.go (L46-57)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-107)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
	params, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	for _, member := range d.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
}

func (d *dummyHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	var msg api.Message
	err := json.Unmarshal(*resp.Result, &msg)
	if err != nil {
		return err
	}
	msg.Body.MessageId = resp.ID
	err = msg.Validate()
	if err != nil {
		return err
	}
	if nodeAddr != msg.Body.Sender {
		return fmt.Errorf("node address %s does not match message sender %s", nodeAddr, msg.Body.Sender)
	}
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(&msg), ErrorCode: api.NoError})
	}
```
