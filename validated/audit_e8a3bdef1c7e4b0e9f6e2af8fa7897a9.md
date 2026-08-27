### Title
Cross-DON message forwarding without per-sender/DON allowlist enforcement in `HandleLegacyUserMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`gateway.(*gateway).ProcessRequest` routes a legacy request purely by the client-supplied `msg.Body.DonId` field: it calls `msg.Validate()` (format-only checks: signature well-formedness, ID/method/DonId length, and `ExtractSigner()` to recover `msg.Body.Sender` from the signature) and then looks up `h, ok = g.handlers[msg.Body.DonId]` [1](#0-0) . `msg.Validate()` never checks that the recovered signer is a member of, or is allowlisted by, the target DON — it only confirms the signature is internally consistent with the message body (which includes `DonId` in the signed payload) [2](#0-1) [3](#0-2) .

Because an attacker signs the message themselves, they can freely choose `DonId="DON_B"` and produce a signature that is "valid" in the sense that `ExtractSigner` recovers their own address — this is not signature forgery, it is the gateway accepting the attacker's own key as a legitimate participant of DON_B purely because they put DON_B's ID in the body.

The request is then dispatched to DON_B's handler via `h.HandleLegacyUserMessage(ctx, msg, callback)`. For the WebAPI capabilities handler, `HandleLegacyUserMessage` performs payload/timestamp/method validation but explicitly has **no allowlist or sender authorization check** before storing state and fanning the message out to every member of DON_B: `// TODO: apply allowlist and rate-limiting here` [4](#0-3) . The handler unconditionally stores a `savedCallback` keyed by `msg.Body.MessageId` and calls `don.SendToNode` for every member of `donConfig.Members` [5](#0-4) .

The existing test suite for this handler explicitly documents this gap: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [6](#0-5) .

By contrast, `gateway_test.go`'s DON-ID tests only assert gateway-level routing behavior (`TestGateway_ProcessRequest_IncorrectDonId`, `TestGateway_ProcessRequest_MissingDonId`) but there is no equivalent handler-level test asserting that a signer not belonging to a DON is rejected by that DON's handler [7](#0-6) .

### Impact Explanation
An attacker whose key is not authorized for DON_B (e.g. only registered/allowlisted with DON A, or even an arbitrary throwaway key) can trigger the WebAPI trigger capability handler for DON_B, causing every node in DON_B to receive and process a spoofed trigger request, and causing the gateway to allocate a `savedCallback` entry keyed on an attacker-chosen `MessageId` for DON_B. This is unauthorized resource consumption/DoS-adjacent abuse of a different DON's per-sender resources and callback state (`savedCallbacks` map, node fan-out), matching the "allowlist bypass" / cross-user resource consumption impact class described in the question. It does not achieve fund movement or credential disclosure, but it is a genuine authorization-bypass allowing an unprivileged/foreign-DON key to consume another DON's handler resources.

### Likelihood Explanation
Minimal precondition: any valid ECDSA key capable of signing a `MessageBody` (no DON A registration is even strictly required for this specific gap, since the capabilities handler performs no sender check at all). The attack is a single HTTP/gateway POST with a self-signed legacy `Message` whose `Body.DonId` is set to the victim DON ID and whose `Method` is `web_api_trigger`. This is fully repeatable and requires no privileged access, matching the "unprivileged attacker" threat model in the prompt.

### Recommendation
Enforce DON-membership/allowlist checks for the sender address recovered by `msg.Validate()` inside each handler's `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage`, not just at the gateway's `handlers[DonId]` routing step. Specifically, in `core/services/gateway/handlers/capabilities/handler.go`, implement the existing `// TODO: apply allowlist and rate-limiting here` before storing `savedCallbacks` and forwarding to `don.SendToNode`, verifying `msg.Body.Sender` against the DON/workflow-specific allowlist (or an equivalent authorizer as used by the vault handler's `requestProcessor.ProcessRequest`/`Authorizer`). Apply per-sender rate limiting keyed by the recovered signer prior to any state mutation or node fan-out.

### Proof of Concept
Go handler-level integration test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Configure two `DONConfig`s, `donA` and `donB`, each instantiated with its own `capabilities.NewHandler`, using distinct member address sets.
2. Generate `keyA` (intended only for DON A) and confirm it is not present in `donB.Members`.
3. Build a `TriggerRequestPayload` and an `api.Message{Body: {DonId: "donB", Method: MethodWebAPITrigger, ...}}`, sign it with `keyA` (`msg.Sign(keyA)`), and call `msg.Validate()` — assert it succeeds (proving the gateway/`msg.Validate()` layer alone does not reject cross-DON signers).
4. Call `donBHandler.HandleLegacyUserMessage(ctx, msg, callback)` directly (bypassing gateway routing, to isolate handler-level authorization).
5. Assert (expected fix behavior): the handler returns an authorization error and does **not** call `don.SendToNode` for any DON_B member, and does **not** insert an entry into `donBHandler.savedCallbacks`.
6. Current behavior (documents the bug): `don.SendToNode` is invoked once per `donB.Members` entry and `savedCallbacks[msg.Body.MessageId]` is populated, despite `keyA` never being allowlisted for DON_B — demonstrating the missing per-handler DON-ID/sender binding.

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

**File:** core/services/gateway/api/message.go (L136-146)
```go
func GetRawMessageBody(msgBody *MessageBody) [][]byte {
	alignedMessageId := make([]byte, MessageIdMaxLen)
	copy(alignedMessageId, msgBody.MessageId)
	alignedMethod := make([]byte, MessageMethodMaxLen)
	copy(alignedMethod, msgBody.Method)
	alignedDonId := make([]byte, MessageDonIdMaxLen)
	copy(alignedDonId, msgBody.DonId)
	alignedReceiver := make([]byte, MessageReceiverLen)
	copy(alignedReceiver, msgBody.Receiver)
	return [][]byte{alignedMessageId, alignedMethod, alignedDonId, alignedReceiver, msgBody.Payload}
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
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
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```

**File:** core/services/gateway/gateway_test.go (L478-496)
```go
func TestGateway_ProcessRequest_MissingDonId(t *testing.T) {
	t.Parallel()

	gw, _ := newGatewayWithMockHandler(t)
	req := newSignedLegacyRequest(t, "abc", "request", "", []byte{})
	response, statusCode := gw.ProcessRequest(t.Context(), req, "")
	requireJSONRPCError(t, response, "abc", jsonrpc.ErrInvalidRequest, "Service name not found: request")
	require.Equal(t, 400, statusCode)
}

func TestGateway_ProcessRequest_IncorrectDonId(t *testing.T) {
	t.Parallel()

	gw, _ := newGatewayWithMockHandler(t)
	req := newSignedLegacyRequest(t, "abc", "request", "unknownDON", []byte{})
	response, statusCode := gw.ProcessRequest(t.Context(), req, "")
	requireJSONRPCError(t, response, "abc", jsonrpc.ErrInvalidParams, "Unsupported DON ID: unknownDON")
	require.Equal(t, 400, statusCode)
}
```
