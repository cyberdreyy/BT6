### Title
Missing sender authorization check in `HandleLegacyUserMessage` allows any signer to broadcast requests to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` validates message format, freshness, and method name, but never checks that `msg.Body.Sender` (the ECDSA-recovered address) is a member of, or an authorized subscriber for, `h.donConfig` before it broadcasts the request to every node in the DON via `don.SendToNode`. This is explicitly acknowledged by a `TODO` comment in the code and confirmed by the actual control flow.

### Finding Description
`HandleLegacyUserMessage` at [1](#0-0)  performs the following steps on an incoming `api.Message`:
1. Unmarshal `TriggerRequestPayload` and validate `Timestamp` is non-zero and not stale.
2. Immediately before the method check, there is a `// TODO: apply allowlist and rate-limiting here` comment [2](#0-1) , confirming no allowlist logic exists at this point.
3. Reject unsupported methods (only `MethodWebAPITrigger` is allowed here).
4. Build a JSON-RPC request from the message via `common.ValidatedRequestFromMessage`.
5. Save a callback keyed by `msg.Body.MessageId`.
6. Loop over **all** `h.donConfig.Members` and call `don.SendToNode(ctx, member.Address, req)` for each one, unconditionally [3](#0-2) .

`msg.Body.Sender` is populated purely from `Message.Validate()`'s call to `ExtractSigner()`, which recovers the address from the ECDSA signature over the message body—it does not check the recovered address against any subscription, allowlist, or DON membership list [4](#0-3) . Any freshly generated private key can produce a message that passes `Validate()`. Since `HandleLegacyUserMessage` never consults `h.donConfig` (or any other authorization source) to check whether `msg.Body.Sender` is permitted to trigger workflows on this DON, an attacker with an arbitrary keypair and no relationship to any subscription can have their payload sent to every node in `h.donConfig.Members`.

### Impact Explanation
This maps to unauthorized capability execution / request-binding bypass (REQUEST_BINDING invariant): an unauthenticated, unaffiliated address can force the gateway to relay an arbitrary `web_api_trigger` payload to every DON node, consuming node compute, HTTP egress (via the corresponding `handleWebAPIOutgoingMessage` path), and rate-limit/allowlist quota under a spoofed but valid-looking identity. This is a genuine authorization gap rather than a misconfiguration, since the code path contains no allowlist enforcement at all, and it is explicitly flagged in-code as a known missing feature.

### Likelihood Explanation
Trivial and fully repeatable: the attacker needs no credentials beyond generating an ECDSA keypair and crafting a message with a fresh timestamp and any `MessageId`/`DonId`/`Payload`, signed with `Message.Sign`. There is no gate (allowlist, subscription check, rate limiting keyed to sender) between `Message.Validate()` succeeding and the broadcast loop in `HandleLegacyUserMessage`. This can be repeated for every DON configured on the gateway.

### Recommendation
Before entering the `don.SendToNode` broadcast loop, validate `msg.Body.Sender` against an authorized-subscriber list (or DON-defined allowlist) for `h.donConfig`, rejecting/erroring out for unauthorized senders, and implement the rate-limiting noted in the `TODO` comment.

### Proof of Concept
Handler-level Go test:
1. Construct a `handler` via `NewHandler` with a `donConfig` containing a set of `Members`, and a mock `handlers.DON` (e.g. from `core/services/gateway/handlers/mocks`) that records calls to `SendToNode`.
2. Generate a fresh ECDSA key unrelated to any configured member/subscriber.
3. Build an `api.Message` with `Body.Method = MethodWebAPITrigger`, a valid `TriggerRequestPayload` with current `Timestamp`, sign it with the fresh key via `msg.Sign(privKey)`.
4. Call `handler.HandleLegacyUserMessage(ctx, msg, mockCallback)`.
5. Assert `mockDON.SendToNode` was invoked once per `donConfig.Members` entry (i.e., broadcast succeeded) despite the signer never appearing in any allowlist/subscriber structure — proving there is no rejection path for unauthorized senders.

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
