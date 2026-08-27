### Title
Legacy Web API handler lacks message-ID replay/duplicate protection, allowing signed-message resubmission to re-trigger execution and drain victim's rate-limit quota - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`Message.Validate()` in `core/services/gateway/api/message.go` performs only structural/signature checks and never binds `Body.MessageId` to freshness or single-use state, which is by design since it is a stateless method. The exploitable gap is that the caller responsible for enforcing single-use semantics, `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`, does not reject a `MessageId` that has already been processed — it only checks payload timestamp staleness and then unconditionally overwrites `savedCallbacks` and re-forwards the message to every DON member.

### Finding Description
`Message.Validate()` [1](#0-0)  checks signature format, ID/method/DonId length, and null-byte suffixes, and recovers the signer, but never checks whether `Body.MessageId` has been seen before or enforces any timestamp binding at this layer.

The gateway's legacy path calls `msg.Validate()` once in `gateway.ProcessRequest` [2](#0-1)  and then dispatches to `handler.HandleLegacyUserMessage`. That handler checks `payload.Timestamp` for staleness against `MaxAllowedMessageAgeSec` [3](#0-2)  but performs no lookup against previously-seen `MessageId`s before storing the callback and forwarding: [4](#0-3) 
The map write at `h.savedCallbacks[msg.Body.MessageId] = ...` unconditionally overwrites any prior entry (there is no `if _, exists := h.savedCallbacks[...]; exists { reject }` guard), and the loop below sends the request to every DON member regardless of whether this exact `MessageId`/signature was already processed.

Because the signature is verified but the message content (including `MessageId` and the embedded `payload.Timestamp`) is fixed at signing time, an attacker who captures one valid signed `Message` (e.g., sniffed gateway traffic) can resubmit the identical bytes as long as `payload.Timestamp` is still within `MaxAllowedMessageAgeSec`, and the gateway will process it as a new request: it re-forwards to all DON members via `don.SendToNode` and consumes the victim's per-sender rate-limit budget in the downstream trigger handler, since `trigger.rateLimiter.Allow(body.Sender)` is keyed on the recovered signer address (the victim), not on message uniqueness [5](#0-4) .

This is a real gap relative to sibling handlers that were later hardened with explicit dedup: `vault/handler.go`'s `requestProcessor.ProcessRequest` rejects "request was already authorized previously" [6](#0-5) , `capabilities/v2/http_trigger_handler.go`'s `setupCallback` rejects in-flight duplicate request IDs [7](#0-6) , and `confidentialrelay/handler.go` rejects "request ID already exists" [8](#0-7) . The legacy `capabilities/handler.go` path has no equivalent.

### Impact Explanation
An attacker who observes one valid signed legacy `Message` (public gateway traffic, logs, or an echoed webhook) can replay it verbatim within the staleness window to: (1) consume the victim's rate-limit allowance in `trigger.go`'s `processTrigger`, denying the victim their own legitimate quota, and (2) cause the gateway to re-forward the request to all DON members, consuming gateway/DON compute and network resources attributable to the victim's identity. This matches the bounty impact class of quota/resource-abuse via request replay/impersonation, since the attacker never possesses the victim's private key yet can force repeated processing under the victim's authorized identity.

### Likelihood Explanation
Preconditions are low-bar and match the threat model: the attacker only needs to observe one prior valid `Message` (no signing key, no privileged access) and resend it before `MaxAllowedMessageAgeSec` (default handling window) elapses. Replay is trivially repeatable — the attacker can resend the exact same bytes multiple times within the window, and each submission independently re-executes `HandleLegacyUserMessage`'s forwarding and rate-limit consumption logic.

### Recommendation
Add explicit single-use enforcement to `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`: before writing to `savedCallbacks`, check whether `msg.Body.MessageId` (or a signature/message-hash tuple) has already been seen within the staleness window and reject with a duplicate-message error, mirroring the pattern used in `vault/handler.go`, `http_trigger_handler.go`, and `confidentialrelay/handler.go`.

### Proof of Concept
1. In `core/services/gateway/handlers/capabilities/handler_test.go`, build a valid signed legacy `web_api_trigger` message with `triggerRequest(...)` (as used in existing tests) with a non-empty payload and a fresh `payload.Timestamp`.
2. Call `handler.HandleLegacyUserMessage(ctx, msg, cb1)` and drain `cb1.Wait` to simulate the DON's normal ACCEPTED response flow, asserting the request was forwarded (`don.SendToNode` invoked once per member).
3. Call `handler.HandleLegacyUserMessage(ctx, msg, cb2)` again with the exact same signed `msg` bytes (same `MessageId`, same signature, unexpired timestamp).
4. Assert (currently failing) that the second call is rejected as duplicate/stale rather than accepted — i.e., expect an error/duplicate response and zero additional `don.SendToNode` calls, rather than the current behavior where `SendToNode` is invoked again for every DON member and `savedCallbacks[msg.Body.MessageId]` is silently overwritten.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
```go
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

**File:** core/capabilities/webapi/trigger/trigger.go (L97-109)
```go
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L723-725)
```go
		// send duplicate request
		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.ErrorContains(t, err, "request was already authorized previously")
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L782-784)
```go
	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
```
