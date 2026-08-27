### Title
Gateway routes messages purely by self-declared `Body.DonId` with no binding between the recovered signer and DON membership - ([File: core/services/gateway/gateway.go], [File: core/services/gateway/api/message.go], [File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`Message.Validate()` calls `ExtractSigner()`/`gw_common.ExtractSigner` only to prove that the request's `Signature` matches the address that signed the (attacker-chosen) `Body`, including the attacker-chosen `Body.DonId` field [1](#0-0) . `gateway.ProcessRequest` then selects the target handler purely from that self-declared `msg.Body.DonId` value (`handlerKey = msg.Body.DonId`) with no check that the recovered `Sender` is a member/allowlisted identity of that DON [2](#0-1) . For the legacy WebAPI trigger handler, once the message reaches `HandleLegacyUserMessage`, there is no sender-based authorization at all — the code has an explicit `// TODO: apply allowlist and rate-limiting here` and forwards the request to every member of the target DON [3](#0-2) .

### Finding Description
An attacker who owns any ECDSA keypair (not necessarily a member of any DON) can construct an `api.Message` with `Body.DonId = "Y"` and sign it themselves. Because `DonId` is part of the signed payload (`GetRawMessageBody` includes `alignedDonId`) [4](#0-3) , `Message.Sign`/`ExtractSigner` will succeed and correctly recover the attacker's own address as `Body.Sender` — this only proves key ownership, not any relationship between that address and DON "Y" [5](#0-4) .

`gateway.ProcessRequest` treats any message with a non-empty `DonId` as a "legacy request", validates the signature, then looks up the handler solely by `handlerKey = msg.Body.DonId` and dispatches to `h.HandleLegacyUserMessage` [6](#0-5) . There is no step anywhere in this path that checks whether the recovered `Sender` address is a registered node/member or an allowlisted external caller for DON "Y".

Downstream, the capabilities WebAPI trigger handler's `HandleLegacyUserMessage` performs payload decoding, timestamp/staleness checks, and method checks, but explicitly defers sender authorization ("apply allowlist and rate-limiting here" is a TODO), then forwards the message to every node in `h.donConfig.Members` for DON Y [3](#0-2) . This confirms that `Message.Validate()`/`ExtractSigner()` is being relied upon as if it were an authorization check, when it is only an authentication (identity) check, and the handler has no independent binding of `Sender` to DON "Y" membership.

### Impact Explanation
This allows an unprivileged/unauthenticated attacker (any address with a keypair) to submit trigger requests that are forwarded to a target DON's full node set as an apparently self-consistent, signature-valid message with an attacker-controlled `Sender`, without the target DON's authorization/allowlist logic ever being invoked (since it's a TODO/not implemented for this handler). This matches Chainlink's "authorization/allowlist bypass leading to unauthorized job/trigger invocation" impact class — the target DON's nodes receive and act on a request from an unauthorized identity as if it were a legitimately scoped request.

### Likelihood Explanation
The precondition is minimal: the attacker needs only any ECDSA keypair (self-generated, no registration required) and network access to the gateway's `/user` (or equivalent) endpoint. No cross-DON legitimate-signer status is even required — the finding generalizes beyond the question's framing, since the handler performs no sender check at all currently. This is fully reproducible and repeatable via a simple crafted+self-signed message.

### Recommendation
Implement DON/handler-level sender authorization independent of the generic `Message.Validate()` signature check: maintain and enforce an allowlist/membership binding between `Body.Sender` and `Body.DonId` (or the specific capability/workflow being invoked) before forwarding to `don.SendToNode`. Complete the noted TODO in `handler.go`'s `HandleLegacyUserMessage` to apply allowlist and rate-limiting per sender before dispatch.

### Proof of Concept
Go handler-level integration test:
1. Configure a gateway with `donConfig.Members` for DON "Y" and instantiate `capabilities.NewHandler` with a mocked `handlers.DON`.
2. Generate an unregistered/arbitrary keypair (not in DON X's or Y's node/member lists), build an `api.Message` with `Body.DonId = "Y"`, `Method = MethodWebAPITrigger`, a valid `TriggerRequestPayload` with current timestamp, and sign it with `msg.Sign(privateKey)`.
3. Call `gateway.ProcessRequest` (or directly `handler.HandleLegacyUserMessage`) with this message.
4. Assert (currently failing/expected-to-fail assertion demonstrating the gap) that `don.SendToNode` is NOT invoked for any member of DON Y, i.e. `mockDon.AssertNotCalled(t, "SendToNode", ...)`; in the current code this assertion fails because `SendToNode` is called for every member without any sender/allowlist check, proving the bypass.

### Citations

**File:** core/services/gateway/api/message.go (L82-87)
```go
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
```

**File:** core/services/gateway/api/message.go (L96-134)
```go
func (m *Message) Sign(privateKey *ecdsa.PrivateKey) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := gw_common.SignData(privateKey, rawData...)
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(crypto.PubkeyToAddress(privateKey.PublicKey).Hex())
	return nil
}

func (m *Message) SignKS(ctx context.Context, ks keys.MessageSigner, signer common.Address) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := ks.SignMessage(ctx, signer, gw_common.Flatten(rawData...))
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(signer.Hex())
	return nil
}

func (m *Message) ExtractSigner() (signerAddress []byte, err error) {
	if m == nil {
		return nil, errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signatureBytes, err := hex.DecodeString(m.Signature)
	if err != nil {
		return nil, err
	}
	return gw_common.ExtractSigner(signatureBytes, rawData...)
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

**File:** core/services/gateway/gateway.go (L250-269)
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

	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
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
