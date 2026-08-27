Confirmed: `msg.Validate()` in `gateway.go` only checks structural properties (signature length, field lengths) and derives `m.Body.Sender` from `ExtractSigner()` — it does not check membership against `donConfig.Members` or any allowlist [1](#0-0) . The gateway's `ProcessRequest` calls `msg.Validate()` and then dispatches directly to `h.HandleLegacyUserMessage(ctx, msg, callback)` for any legacy DON-ID request, with no allowlist check in between [2](#0-1) . Inside `HandleLegacyUserMessage`, the only checks performed are payload decoding, timestamp presence, staleness, and method name — right before the `TODO: apply allowlist and rate-limiting here` comment, after which the request is forwarded via `don.SendToNode` to every member in `h.donConfig.Members` [3](#0-2) . There is no check anywhere in this path that `msg.Body.Sender` (the recovered signer) belongs to any known/allowed set of identities.

### Title
Missing allowlist check in `HandleLegacyUserMessage` allows any signer to invoke `web_api_trigger` on all DON nodes - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` forwards any structurally-valid, freshly-signed `api.Message` with `Method=web_api_trigger` to every node in `donConfig.Members` without checking the recovered signer against any allowlist, exactly as marked by the `TODO: apply allowlist and rate-limiting here` comment. Since the gateway's `msg.Validate()` only checks signature format/length and other field lengths (never membership), an attacker with no API token, no external-initiator credential, and no DON membership can trigger arbitrary web API trigger capability calls on the DON.

### Finding Description
The attacker crafts an `api.Message` with `Body.Method = "web_api_trigger"`, a fresh `Body.DonId` matching a real DON, a fresh `Body.MessageId`, and a `TriggerRequestPayload` with `Timestamp` set to current time, then signs it with any never-before-seen ECDSA key using `Sign()` [4](#0-3) . This request reaches `gateway.ProcessRequest`, which treats it as a legacy request (`msg.Body.DonId != ""`), calls `msg.Validate()` (structural + signature-recoverability check only, no allowlist), looks up the handler by `DonId`, and calls `h.HandleLegacyUserMessage(ctx, msg, callback)` [2](#0-1) . Inside `HandleLegacyUserMessage`, the code checks payload decoding, `Timestamp != 0`, and message staleness, then hits the `TODO: apply allowlist and rate-limiting here` comment with no actual allowlist enforcement, checks only that `msg.Body.Method == MethodWebAPITrigger`, converts the message to a request, and loops over `h.donConfig.Members` calling `don.SendToNode(ctx, member.Address, req)` for every member [5](#0-4) . No code path checks `msg.Body.Sender` (the address recovered from the signature during `Validate()`) against `donConfig.Members`, any external-initiator credential store, or any other allowlist before dispatching to all DON nodes.

### Impact Explanation
This matches "unauthorized job run" / capability invocation impact: any unauthenticated attacker who can sign a message with an arbitrary keypair can trigger the `web_api_trigger` capability across the entire DON's node set, bypassing the intended access control model entirely. This is a full authorization bypass on the capability's entrypoint, letting an anonymous party invoke workflow triggers meant to be gated by an allowlist.

### Likelihood Explanation
Highly likely and trivially repeatable: the attacker needs no privileges — not a DON member, no API token, no external-initiator credential. Constructing and signing an `api.Message` requires only a locally generated ECDSA key (via `crypto.GenerateKey` and `Sign`), and the HTTP gateway endpoint accepts and routes it. The bug is deterministic — every call to `HandleLegacyUserMessage` for `web_api_trigger` skips the allowlist regardless of sender identity.

### Recommendation
Before dispatching to `don.SendToNode`, verify `msg.Body.Sender` against an authorized allowlist (e.g., an external-initiator/API-key mapping or a DON-configured caller allowlist) and apply rate-limiting keyed on the sender, rejecting the request with `api.UnauthorizedError` (or similar) if the sender is not permitted, replacing the `TODO` comment with actual enforcement logic in `core/services/gateway/handlers/capabilities/handler.go`.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a `donConfig` containing a fixed set of `Members` (known addresses) and a fake/mock `handlers.DON` (e.g. `handlermocks.NewDON(t)`).
2. Generate a brand-new ECDSA key via `crypto.GenerateKey()` that is not in `donConfig.Members` and not part of any allowlist config.
3. Build an `api.Message` with `Body.Method = MethodWebAPITrigger`, `Body.DonId` matching `donConfig.DonId`, a valid `TriggerRequestPayload{Timestamp: time.Now().Unix()}` payload, and sign it with the new key via `msg.Sign(privateKey)`.
4. Call `msg.Validate()` to populate `Body.Sender` (simulating gateway behavior), then call `h.HandleLegacyUserMessage(ctx, msg, callback)`.
5. Assert that the mock `don.SendToNode` was invoked once per `donConfig.Members` entry (`don.AssertCalled(t, "SendToNode", ...)` for each member address), proving the message was dispatched to all DON nodes despite the signer never being registered anywhere — confirming no allowlist check exists in the code path.

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

**File:** core/services/gateway/api/message.go (L96-108)
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
```

**File:** core/services/gateway/gateway.go (L250-273)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-420)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
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
