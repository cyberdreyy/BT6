Confirmed: there is no per-sender/per-DON allowlist field (`DONConfig` has no `AllowedSenders`) and the routing/authorization comment in the handler explicitly marks this as unimplemented.

### Title
Attacker-controlled `Body.DonId` allows cross-DON routing with no sender-to-DON membership check - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The gateway's legacy user-message path selects the target DON handler solely from the attacker-supplied `Message.Body.DonId` field, and `HandleLegacyUserMessage` never verifies that the message's signer is an authorized member/subscriber of that DON before broadcasting the request to all of that DON's nodes. As a result, any holder of a valid ECDSA signing key can address and trigger any configured DON's handler, not just the one they are meant to use.

### Finding Description
`gateway.ProcessRequest` decodes the incoming message and, for legacy requests, uses `msg.Body.DonId` directly as the lookup key into `g.handlers` to select the handler instance to invoke: [1](#0-0) 

`msg.Validate()` only checks message formatting and recovers the signer address into `msg.Body.Sender` — it performs no authorization check tying the signer to the target `DonId`: [2](#0-1) 

Inside the capabilities handler, `HandleLegacyUserMessage` processes the message and broadcasts it to every member of `h.donConfig.Members` — but the code explicitly documents that sender allowlisting is not implemented: [3](#0-2) [4](#0-3) 

The `DONConfig` struct that backs each handler instance has no `AllowedSenders`/membership field at all: [5](#0-4) 

By contrast, the node-to-gateway WebSocket connection is authenticated per-DON (the `AuthHeaderElems.DonId` is checked against the connection's expected DON during handshake), but that check only protects the node→gateway side, not the user→gateway (`HandleLegacyUserMessage`) side: [6](#0-5) [7](#0-6) 

So the exploit flow is: an attacker with any valid signing key crafts `Message{Body: {DonId: "DON_B", Method: "web_api_trigger", ...}}`, signs it, and sends it to the gateway's single shared user-facing endpoint. `ProcessRequest` looks up `g.handlers["DON_B"]` and hands it to that handler's `HandleLegacyUserMessage`, which validates format/staleness/method only, then broadcasts the payload to all of DON B's node members — with zero verification that the signer belongs to (or is even known to) DON A or DON B.

### Impact Explanation
This allows cross-DON request injection/impersonation: a party legitimately provisioned only for DON A (or in fact any arbitrary key holder, since there is no allowlist at all in this handler) can trigger web-API/trigger workflows on DON B's nodes, consuming DON B's resources, potentially triggering unintended job runs, or acting as a foothold for further abuse of unrelated DON tenants. This maps to the Chainlink bounty class of unauthorized job/run triggering via authorization bypass in the gateway.

### Likelihood Explanation
The only precondition is possession of any ECDSA private key usable to produce a valid `Message` signature — the code does not require the key to be registered anywhere for this handler's DON. This is trivially and repeatably exploitable via a scripted HTTP/JSON-RPC request; no privileged network position or node compromise is required.

### Recommendation
Add and enforce a sender allowlist per DON/handler (e.g., `DONConfig.AllowedSenders` or equivalent), checked in `HandleLegacyUserMessage` (and the newer JSON-RPC path) before any message is queued/broadcast to `don.SendToNode`. Reject requests whose recovered `msg.Body.Sender` is not authorized for the specific `DonId`/handler instance that is about to process them, closing the "TODO: apply allowlist and rate-limiting here" gap.

### Proof of Concept
Go handler-level integration test:
1. Instantiate two `capabilities.handler` instances via `NewHandler`, one for `donConfig{DonId:"DON_A", Members:[...]}` and one for `donConfig{DonId:"DON_B", Members:[...]}`, wired into a `gateway` (or directly via `g.handlers` map as in `gateway.go`).
2. Generate a signing key that is (conceptually) associated only with DON A (no code currently enforces this, but simulate intent).
3. Build `msg := api.Message{Body: api.MessageBody{MessageId: "1", Method: MethodWebAPITrigger, DonId: "DON_B", Payload: validTriggerPayload}}`, sign with the DON-A key, call `msg.Validate()`.
4. Call `gateway.ProcessRequest` (or directly `donBHandler.HandleLegacyUserMessage`) with this message.
5. Assert (currently failing): expect rejection (`api.UnsupportedDONIdError`/authorization error) because the signer is not authorized for DON B.
6. Observe actual current behavior: `don.SendToNode` mock records calls to all of DON B's `Members`, proving the message was accepted and forwarded despite the signer having no relationship to DON B — confirming the missing cross-check.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
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

**File:** core/services/gateway/config/config.go (L34-41)
```go
type DONConfig struct {
	DonId         string
	HandlerName   string          // Deprecated: use Handlers instead
	HandlerConfig json.RawMessage // Deprecated: use Handlers instead
	Members       []NodeConfig
	F             int
	Handlers      []Handler
}
```

**File:** core/services/gateway/network/handshake.go (L44-49)
```go
// Components going into the auth header, excluding the signature.
type AuthHeaderElems struct {
	Timestamp uint32
	DonId     string
	GatewayId string
}
```

**File:** core/services/gateway/connectionmanager_test.go (L174-178)
```go
	// invalid DON ID
	badAuthHeaderElems := authHeaderElems
	badAuthHeaderElems.DonId = "my_don_2"
	_, _, err = mgr.StartHandshake(signAndPackAuthHeader(t, &badAuthHeaderElems, nodes[0].PrivateKey))
	require.ErrorIs(t, err, network.ErrAuthInvalidDonId)
```
