Confirmed: the `capabilities` handler's `HandleLegacyUserMessage` performs no sender allowlist/subscription check anywhere in its path, and `common.ValidatedRequestFromMessage` only validates structural fields (nil message, message ID, method), never sender identity.

### Title
Missing sender authorization allows unauthenticated broadcast to DON in `HandleLegacyUserMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` accepts any syntactically valid, signed `api.Message` and forwards it to every member of `h.donConfig.Members` without ever checking `msg.Body.Sender` against an allowlist or Functions subscription registry. `common.ValidatedRequestFromMessage` only validates message-structure fields (nil message, non-empty `MessageId`/`Method`), never sender authorization, so any holder of an arbitrary ECDSA key can trigger DON execution.

### Finding Description
In `HandleLegacyUserMessage` [1](#0-0) , the message payload is decoded, timestamp/staleness and method name are checked, and then `common.ValidatedRequestFromMessage(msg)` is called before broadcasting to all `h.donConfig.Members` via `don.SendToNode`. The code explicitly contains the comment `// TODO: apply allowlist and rate-limiting here` [2](#0-1)  immediately before the method check, confirming no allowlist logic has been implemented on this path.

`msg.Validate()` (invoked earlier by the gateway before dispatching to the handler) only checks structural constraints (signature length, ID/method/DonId/receiver length limits) and extracts the signer address into `m.Body.Sender` via `ExtractSigner`, but performs no lookup against any subscription or allowlist registry [3](#0-2) . Similarly `common.ValidatedRequestFromMessage` only checks `msg == nil`, empty `MessageId`, and empty `Method` — nothing sender-related [4](#0-3) .

As a result, any attacker who can produce a valid ECDSA signature over the message body (which requires nothing more than generating their own keypair) satisfies `Validate()` and `ValidatedRequestFromMessage`, and the message is broadcast to every DON member unconditionally: `for _, member := range h.donConfig.Members { err = errors.Join(err, don.SendToNode(ctx, member.Address, req)) }` [5](#0-4) .

### Impact Explanation
This allows unauthorized DON execution: any external party with an arbitrary (non-allowlisted, non-subscribed) EOA key can submit a `web_api_trigger` request that gets forwarded to all DON nodes, consuming node/HTTP-outbound resources and capability execution regardless of Functions subscription/allowlist status. This maps to the Chainlink bounty impact class of authorization/allowlist bypass leading to unauthorized job/capability execution.

### Likelihood Explanation
Feasibility is high and requires no privileges: the attacker only needs to generate an ECDSA keypair and sign a well-formed message per `GetRawMessageBody`/`Sign` rules [6](#0-5) , which is fully repeatable per request and does not depend on being a registered/allowlisted sender.

### Recommendation
Implement the allowlist/subscription check referenced by the `TODO` comment: before calling `ValidatedRequestFromMessage`/broadcasting, look up `msg.Body.Sender` against the DON's configured allowlist or the Functions subscription registry, and reject with an authorization error (e.g., a new `api.ErrorCode` such as `UnauthorizedSenderError`) if the sender is not permitted.

### Proof of Concept
Go handler-level test plan (extending `handler_test.go`):
1. Reuse `setupHandler(t)` to build a `handler` with a `donConfig` whose `Members` list does NOT include the test signer's address, and without any allowlist configured (mirroring current behavior since none exists).
2. Generate a fresh, unrelated ECDSA key (`crypto.GenerateKey()`) not present in any Functions subscription or DON allowlist.
3. Build a `triggerRequest` message using this key (`triggerRequest(t, unrelatedKey, ...)`), signing per existing helper.
4. Set up `don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)` for each `h.donConfig.Members` entry.
5. Call `handler.HandleLegacyUserMessage(ctx, msg, cb)` and assert `require.NoError(t, err)`.
6. Assert `don.AssertCalled(t, "SendToNode", ...)` was invoked once per DON member (matching current `TestHandlerReceiveHTTPMessageFromClient` "happy case" pattern at [7](#0-6) ), demonstrating broadcast occurs for an arbitrary, non-allowlisted signer with no authorization error returned.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-421)
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

**File:** core/services/gateway/api/message.go (L90-108)
```go
// Message signatures are over the following data:
//  1. MessageId aligned to 128 bytes
//  2. Method aligned to 64 bytes
//  3. DonId aligned to 64 bytes
//  4. Receiver (in hex) aligned to 42 bytes
//  5. Payload (raw bytes before parsing)
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

**File:** core/services/gateway/handlers/common/message_util.go (L82-105)
```go
// ValidatedRequestFromMessage converts a legacy Gateway Message to a JSON-RPC request
func ValidatedRequestFromMessage(msg *api.Message) (*jsonrpc.Request[json.RawMessage], error) {
	if msg == nil {
		return nil, errors.New("nil message")
	}
	if msg.Body.MessageId == "" {
		return nil, errors.New("message ID is empty")
	}
	if msg.Body.Method == "" {
		return nil, errors.New("method is empty")
	}
	params, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal message: %w", err)
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	return req, nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L242-255)
```go
	t.Run("happy case", func(t *testing.T) {
		// sends to 2 dons
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()

		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, msg, cb)
		require.NoError(t, err)
```
