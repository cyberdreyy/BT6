### Title
Missing sender-to-DON allowlist binding in legacy WebAPI capability requests allows cross-DON request forwarding - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`gateway.ProcessRequest` routes any legacy `api.Message` to the handler keyed purely by the attacker-controlled `msg.Body.DonId`, after `msg.Validate()` only checks structural fields and derives `Sender` from the signature — it never verifies the signer is authorized for that DON. The target `capabilities` handler's `HandleLegacyUserMessage` explicitly defers allowlisting with a `TODO` and forwards the message to all DON members without any sender-authorization check.

### Finding Description
In `core/services/gateway/gateway.go`, `(*gateway).ProcessRequest` (lines 250-262) treats any message with a non-empty `Body.DonId` as a "legacy request," calls `msg.Validate()`, and then looks up the handler solely by `handlerKey = msg.Body.DonId`: [1](#0-0) 

`msg.Validate()` in `core/services/gateway/api/message.go` only validates field lengths/format and extracts `m.Body.Sender` from the ECDSA signature via `ExtractSigner()` — it performs no check that the derived sender is subscribed/allowlisted for `m.Body.DonId`: [2](#0-1) 

The routed-to handler, `(*handler).HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`, validates payload timestamp/method but explicitly skips authorization with a `TODO: apply allowlist and rate-limiting here`, then fans the message out to every node in `h.donConfig.Members`: [3](#0-2) 

Any attacker holding an ECDSA keypair can self-sign a well-formed `api.Message` (correct signature over their own key, satisfying `ExtractSigner`), set `Body.DonId` to any DON ID configured in `g.handlers`, and POST it to the gateway. Since routing key and allowlist enforcement are both absent for legacy requests in this handler, the message is accepted and broadcast to that victim DON's member nodes, invoking `MethodWebAPITrigger` processing (subsequent outbound HTTP requests via `handleWebAPIOutgoingMessage`) that should only be reachable by DON-subscribed/allowlisted senders.

### Impact Explanation
This matches the "unauthorized job run" / "allowlist bypass" bounty class: an attacker with no DON subscription can trigger workflow/webapi actions on a victim DON, causing that DON (and its subscribers) to execute unauthorized work and outbound HTTP requests (`handleWebAPIOutgoingMessage`) charged/attributed to the victim DON — a cross-tenant resource-consumption and allowlist-bypass issue.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs any valid ECDSA keypair (no DON registration, no credentials) and knowledge of a target `DonId` string, which is often discoverable/predictable from configuration or prior legitimate traffic. The request is a single signed HTTP POST to the gateway's user-facing endpoint, and it is fully repeatable since there is no per-sender/DON binding check anywhere in this code path — confirmed by the explicit `TODO` comment in the shipped handler code.

### Recommendation
Enforce sender-to-DON authorization for legacy messages before dispatch: in `HandleLegacyUserMessage` (or upstream in `ProcessRequest` immediately after `msg.Validate()`), check that `msg.Body.Sender` is present in the target DON's configured allowlist/subscriber set for that `DonId`/method before forwarding to `don.SendToNode`. This closes the TODO in `core/services/gateway/handlers/capabilities/handler.go` and adds request-binding validation in `core/services/gateway/gateway.go`.

### Proof of Concept
Go handler-level integration test plan:
1. Configure two DONs, `donA` (attacker not a member/allowlisted) and `donB` (victim), each with its own `capabilities.handler` instance and `donConfig.Members`.
2. Generate an attacker ECDSA keypair not registered/allowlisted for either DON.
3. Build an `api.Message` with `Body.DonId = "donB"`, a valid `MethodWebAPITrigger` payload, and sign it with the attacker's key via `msg.Sign(attackerKey)`.
4. Call `gateway.ProcessRequest` (or directly `donBHandler.HandleLegacyUserMessage`) with this message.
5. Assert expectation: request should be rejected with an authorization/allowlist error.
6. Actual (current) behavior: `msg.Validate()` succeeds, handler lookup by `DonId` succeeds, and `HandleLegacyUserMessage` forwards the message to all of `donB`'s `donConfig.Members` via `don.SendToNode` — demonstrating the missing binding check [4](#0-3) .

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
