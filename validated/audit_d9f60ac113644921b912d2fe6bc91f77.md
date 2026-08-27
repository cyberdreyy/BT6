### Title
Missing sender allowlist check in gateway capabilities handler allows unauthorized workflow trigger dispatch - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`(*Message).Validate()` in `core/services/gateway/api/message.go` only checks field lengths/null-byte suffixes and signature format, then extracts the signer address into `Body.Sender` — it performs no authorization check against any allowlist. `gateway.go`'s `ProcessRequest` calls this `Validate()` and, on success, dispatches directly to `handler.HandleLegacyUserMessage`, which itself has an explicit `// TODO: apply allowlist and rate-limiting here` and forwards the request to every DON member via `don.SendToNode` without checking `msg.Body.Sender` against any authorized set.

### Finding Description
The reachable path is: an attacker POSTs a signed legacy `Message` to the gateway HTTP endpoint → `gateway.ProcessRequest` decodes it and, since `msg.Body.DonId != ""`, treats it as legacy, calling `msg.Validate()` [1](#0-0)  . `Validate()` only checks signature hex length, field lengths, null-byte suffixes, and recovers the signer via `ExtractSigner()`, setting `Body.Sender` — no check against a list of authorized senders is performed [2](#0-1) . Any freshly generated ECDSA keypair can sign a syntactically valid message and pass this check, as demonstrated by the test helper `newSignedLegacyRequest` which simply generates a random key [3](#0-2) .

After `Validate()` succeeds, the message is looked up by `DonId` in `g.handlers` and passed straight to `h.HandleLegacyUserMessage(ctx, msg, callback)` [4](#0-3) . In the capabilities handler implementation, after payload decoding, timestamp/staleness, and method checks, the code contains the literal comment `// TODO: apply allowlist and rate-limiting here` immediately before checking only that `msg.Body.Method == MethodWebAPITrigger`, with no check of `msg.Body.Sender` against a DON-configured allowlist [5](#0-4) . The request is then converted and forwarded to all DON members with `don.SendToNode` [6](#0-5) .

The handler's own test file confirms this gap is a known, unaddressed area: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [7](#0-6) , and none of the existing "sad case" subtests (invalid method, stale message, empty/invalid payload) test for sender authorization/allowlist rejection [8](#0-7) .

### Impact Explanation
Any unprivileged party possessing an arbitrary ECDSA keypair (no DON membership, no prior authorization) can craft and sign a legacy `Message` with `Method = web_api_trigger` and a valid `DonId`, and have it dispatched to every node in that DON via `don.SendToNode`. This causes unauthorized workflow-trigger requests to reach DON nodes, matching the "unauthorized job/workflow trigger dispatch" bounty impact class — the nodes receive and process attacker-originated trigger requests that were never vetted against any legitimate sender allowlist at the gateway layer.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only to generate an ECDSA keypair (trivial, no cost) and know/guess a valid `DonId` (which is discoverable, e.g. via configuration or the legacy service-name mapping). No credentials, DON membership, or prior relationship with the network are required. The exploit is fully repeatable — each request just needs a fresh valid signature over the message body fields, which `Sign()` computes deterministically from public inputs.

### Recommendation
Implement the sender allowlist check called out by the `// TODO: apply allowlist and rate-limiting here` comment in `core/services/gateway/handlers/capabilities/handler.go`, before forwarding to `don.SendToNode`: verify `msg.Body.Sender` (populated by `Validate()`/`ExtractSigner()`) against a per-DON/per-workflow authorized sender set (and enforce rate limiting) before accepting `MethodWebAPITrigger` requests. This check must happen inside `HandleLegacyUserMessage` (or before it in `gateway.ProcessRequest`) and must reject unauthorized senders with an explicit error response rather than silently forwarding to nodes.

### Proof of Concept
Handler-level integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Set up the handler with a `donConfig` containing a fixed set of DON members, but do **not** register the attacker's address in any allowlist.
2. Generate a fresh, unrelated ECDSA key (`crypto.GenerateKey()`), sign a `Message` with `Method: MethodWebAPITrigger`, valid `DonId`, and a valid `TriggerRequestPayload` (non-stale timestamp).
3. Mock or spy on `don.SendToNode` (via the `don` test double already used in `handler_test.go`) to assert it is **not** called for this unauthorized sender.
4. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)` and assert the callback receives an authorization-error response (e.g., `api.HandlerError` or new `api.UnauthorizedError`) rather than being silently forwarded.
5. Expected current (failing) behavior: `don.SendToNode` is invoked for every DON member despite the sender not being allowlisted, demonstrating the gap; after the fix, the request should be rejected before reaching `don.SendToNode`.

### Citations

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

**File:** core/services/gateway/gateway_test.go (L410-428)
```go
// newSignedLegacyRequest creates a signed legacy request message for testing purposes.
// Legacy requests embed
func newSignedLegacyRequest(t *testing.T, messageID string, method string, donID string, payload []byte) []byte {
	msg := &api.Message{
		Body: api.MessageBody{
			MessageId: messageID,
			Method:    method,
			DonId:     donID,
			Payload:   payload,
		},
	}
	privateKey, err := crypto.GenerateKey()
	require.NoError(t, err)
	require.NoError(t, msg.Sign(privateKey))
	codec := api.JsonRPCCodec{}
	rawRequest, err := codec.EncodeLegacyRequest(msg)
	require.NoError(t, err)
	return rawRequest
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-396)
```go
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L267-338)
```go
	t.Run("sad case invalid method", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)

		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method foo",
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		}, r)
	})

	t.Run("sad case stale message", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		}, r)
	})

	t.Run("sad case empty payload", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "{}")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				"error decoding payload field params in TriggerRequestPayload: required",
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		}, r)
	})

	t.Run("sad case invalid payload", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				"error decoding payload field params in TriggerRequestPayload: required",
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		}, r)
	})
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
