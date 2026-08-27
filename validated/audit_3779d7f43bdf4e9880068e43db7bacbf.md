Confirmed: `MessageId` is the client-supplied `jsonrpc2.Request.ID` (set directly by `DecodeJSONRequest` at [1](#0-0) ), so an unprivileged client fully controls this key.

### Title
Cross-user response hijack via unauthenticated MessageId collision in gateway legacy request callback map - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`handler.HandleLegacyUserMessage` stores each in-flight callback in a shared map keyed only by the attacker-controlled `MessageId`, with an unconditional overwrite instead of a "reserve-if-absent" check. This mirrors the root cause of the Astaria reentrancy bug: a later, attacker-triggered state write silently clobbers state tied to an earlier, legitimate in-flight operation, so when the async completion path (the node's trigger response) fires, it resolves against the attacker's saved state rather than the original requester's.

### Finding Description
`HandleLegacyUserMessage` unconditionally assigns into the shared `savedCallbacks` map under lock: [2](#0-1) 

There is no check for an existing entry before the write (contrast with `AddHandler`'s "check all, then set all" pattern used elsewhere: [3](#0-2) ). Later, when a node replies with the trigger result, the gateway looks the callback up purely by `MessageId`, pops it, and delivers the response to whoever is registered: [4](#0-3) 

The `MessageId` is fully attacker-controlled: it is copied verbatim from the client's JSON-RPC request `ID` field during decoding, with no server-side uniqueness enforcement: [1](#0-0) 
`Message.Validate` only checks length/format constraints on `MessageId`, not uniqueness or ownership: [5](#0-4) 

This is structurally analogous to the Astaria bug: in both cases, a piece of shared, keyed state representing an in-progress privileged operation (the Lien token / the saved HTTP callback) can be overwritten mid-flight by an attacker-controlled follow-up action using the same identifier, so that the eventual "completion" callback (`endAuction` finalizing the wrong lien / the gateway resolving the wrong callback) operates on attacker-supplied state instead of the original.

### Impact Explanation
If an unprivileged client learns or guesses another client's in-flight `MessageId` (e.g., because it's client-generated and not required to be random/secret, or through timing/log exposure) and sends a request with the same ID to the same DON before the original completes, the victim's node-side trigger response will be delivered to the attacker's saved callback instead of the victim's — a concrete cross-user response confusion. Depending on what data the trigger response carries (e.g., web API trigger payloads), this could leak response data intended for a different user's request to the attacker, or cause the victim's request to hang/never complete while the attacker's request is falsely satisfied.

### Likelihood Explanation
Exploitation requires the attacker to know/predict the victim's `MessageId` value and race the window between the victim's request being registered and the corresponding node response arriving. Since `MessageId` is client-chosen (often just an incrementing counter or fixed value from SDKs) rather than a server-issued random/secret token, collisions are plausible in practice for automated or scripted clients, but it does require some knowledge of concurrent traffic, making likelihood medium rather than trivial.

### Recommendation
1. In `HandleLegacyUserMessage`, only insert into `savedCallbacks` if no entry exists for `MessageId` (return an error/reject duplicate IDs), analogous to `AddHandler`'s check-then-set pattern.
2. Bind the saved callback to additional context (e.g., a server-generated nonce or the underlying connection/session identity) rather than relying solely on client-supplied `MessageId` for correlation.
3. Consider using server-generated correlation IDs, or requiring high-entropy random IDs, to make collision guessing infeasible.

### Proof of Concept
1. Client A sends a `web_api_trigger` HTTP request with `MessageId = "X"`; gateway stores `savedCallbacks["X"] = callbackA` and forwards to DON nodes.
2. Before a node responds, malicious Client B sends its own legacy request also using `MessageId = "X"`; `HandleLegacyUserMessage` overwrites `savedCallbacks["X"] = callbackB` (no existence check) at [2](#0-1) .
3. When a DON node later sends its `web_api_trigger` response for ID `"X"` (intended for Client A's request), `handleWebAPITriggerMessage` looks up and deletes `savedCallbacks["X"]`, now `callbackB`, and delivers the response there: [4](#0-3) .
4. Client B (attacker) receives the response meant for Client A; Client A's original callback never fires and eventually times out.

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

**File:** core/services/gateway/connector/connector.go (L160-176)
```go
func (c *gatewayConnector) AddHandler(ctx context.Context, methods []string, handler core.GatewayConnectorHandler) error {
	if handler == nil {
		return errors.New("cannot add a nil handler")
	}
	c.handlersMu.Lock()
	defer c.handlersMu.Unlock()
	for _, method := range methods {
		if _, exists := c.handlers[method]; exists {
			return fmt.Errorf("handler for method %s already exists", method)
		}
	}
	// add all or nothing
	for _, method := range methods {
		c.handlers[method] = handler
	}
	return nil
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
