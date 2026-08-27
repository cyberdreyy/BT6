Now I have enough context to render a verdict.

msg.Validate() only checks a well-formed hex signature that recovers to *some* address via ECDSA recovery — it does not check that the recovered signer is a specific allowlisted or privileged identity. [1](#0-0)  Any attacker holding an arbitrary ECDSA private key can therefore generate a self-signed message that passes `msg.Validate()` in `gateway.ProcessRequest`, since that only requires `len(m.Signature)==65` and successful recovery, not membership in any allowlist. [2](#0-1)  This confirms the handler code itself explicitly documents the missing authorization: `// TODO: apply allowlist and rate-limiting here` right before the method-name check, and there is no authentication/authorization/allowlist check anywhere else in `HandleLegacyUserMessage`. [3](#0-2)  After passing that trivial check, the function unconditionally iterates `h.donConfig.Members` and calls `don.SendToNode` for every member regardless of any prior authorization outcome, joining all resulting errors. [4](#0-3)  The existing unit test confirms the fan-out to `SendToNode` for every DON member happens on every valid legacy trigger message. [5](#0-4) 

### Title
Unauthenticated/self-signed `web_api_trigger` messages fan out to every DON member with no allowlist check - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` only validates message structure, a fresh (attacker-controlled) ECDSA signature, and a non-stale timestamp before broadcasting the request to every configured DON member via `don.SendToNode`. There is no allowlist, credential, or membership check enforced before this fan-out, as explicitly flagged by the in-code `TODO: apply allowlist and rate-limiting here` comment.

### Finding Description
`gateway.ProcessRequest` decodes the raw JSON-RPC request and, for legacy (DON-ID-bearing) requests, calls `msg.Validate()` before dispatching to `HandleLegacyUserMessage`. [6](#0-5)  `Message.Validate()` verifies field-length constraints and that the signature recovers to *an* address via `ExtractSigner`; it does not check that the recovered address belongs to any known/permitted user, workflow owner, or DON member. [1](#0-0)  Any attacker who can reach the gateway's user-facing HTTP endpoint can generate a fresh keypair, sign an arbitrary `TriggerRequestPayload` (`webapicap.TriggerRequestPayload`) with a valid timestamp, and pass `Validate()` with zero prior credential. In `HandleLegacyUserMessage`, after payload decoding, timestamp/staleness checks, and a method-name check, the code explicitly notes the missing allowlist enforcement (`// TODO: apply allowlist and rate-limiting here`) and proceeds directly to `for _, member := range h.donConfig.Members { err = errors.Join(err, don.SendToNode(ctx, member.Address, req)) }`, unconditionally contacting every DON member regardless of any authorization result. [7](#0-6)  This mirrors the dummy handler's identical fan-out pattern used elsewhere. [8](#0-7) 

### Impact Explanation
This allows any network-reachable, unauthenticated (self-signed) client to trigger message delivery to the entire set of DON member addresses configured for the target `don_id`, and to observe per-member success/failure/timing from the joined error, revealing DON topology/membership information and causing every DON node to process and act on an attacker-supplied trigger payload. This matches an information-disclosure and unauthorized-DON-interaction impact class — it does not, on its own, grant fund movement or key disclosure, but is a legitimate authorization/allowlist bypass for gateway-to-DON traffic that the code itself flags as an open TODO.

### Likelihood Explanation
The only precondition is network reachability to the gateway's user HTTP port and the ability to generate an ECDSA keypair — trivial and requires no privileged credential, DON membership, or allowlist entry. The `msg.Validate()` gate is satisfied by any self-signed, well-formed, non-stale message, making this fully reproducible and repeatable at will.

### Recommendation
Implement the allowlist/authorization check called out by the existing `// TODO: apply allowlist and rate-limiting here` comment in `HandleLegacyUserMessage` before performing the `don.SendToNode` fan-out — e.g., verify the recovered `msg.Body.Sender` against a workflow-owner/user allowlist (as already done for the newer JSON-RPC vault pipeline via `Authorizer.AuthorizeRequest`) and reject/rate-limit unauthorized senders prior to contacting any DON member.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Use `setupHandler(t)` to build a `handler` with a mocked `don` (`handlers.DON` mock) and a `donConfig` containing N members.
2. Generate a fresh, non-registered `ecdsa.PrivateKey` (not among any configured allowlist/DON member) and build `msg := triggerRequest(t, freshKey, topics, "", "", "")`.
3. Assert `msg.Validate()` succeeds despite the key not being registered anywhere.
4. Set up `don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(errors.New("simulated failure")).Times(N)`.
5. Call `err := handler.HandleLegacyUserMessage(ctx, msg, hc.NewCallback())`.
6. Assert `don.AssertNumberOfCalls(t, "SendToNode", N)` — i.e., `SendToNode` was invoked once per `donConfig.Members` entry with each member's `Address` even though every call failed — confirming the unauthenticated attacker triggered fan-out probing of all DON member addresses with no allowlist gate.

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

**File:** core/services/gateway/handlers/handler.dummy.go (L62-82)
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
```
