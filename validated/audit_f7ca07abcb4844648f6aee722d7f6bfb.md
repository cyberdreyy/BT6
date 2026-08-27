### Title
Attacker-controlled `MessageId` collision in `savedCallbacks` map allows cross-user callback hijacking - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores the caller's `Callback` in `h.savedCallbacks[msg.Body.MessageId]` without checking for an existing entry or scoping the key by `Sender`. Since `MessageId` is a client-chosen string embedded in the signed message body (not derived from the signature), any two unrelated, unauthenticated senders can pick the same `MessageId`, letting a later request silently overwrite an earlier pending callback.

### Finding Description
The gateway HTTP entrypoint `gateway.ProcessRequest` decodes the JSON-RPC request, validates the message signature via `msg.Validate()` (which recovers `Sender` from the ECDSA signature but does not touch or constrain `MessageId`), and dispatches to `handler.HandleLegacyUserMessage(ctx, msg, callback)`. [1](#0-0) 

`MessageId` is a fully attacker-chosen field of `MessageBody`, only constrained by length/null-byte checks in `Message.Validate`; it plays no role in signature-based identity (`Sender` is derived independently from `ExtractSigner`). [2](#0-1) 

In `HandleLegacyUserMessage`, after payload/timestamp/method validation, the callback is stored keyed purely by `msg.Body.MessageId`, with an unconditional overwrite and no existence check or per-sender scoping: [3](#0-2) 

Later, when the DON responds via `HandleNodeMessage` → `handleWebAPITriggerMessage`, the response is routed strictly by `msg.Body.MessageId` looked up in `savedCallbacks`, with no correlation back to which `Sender` originally registered that ID: [4](#0-3) 

Exploit flow:
1. Victim sends a signed `HandleLegacyUserMessage` request with `MessageId = "dup1"`, registering `savedCallbacks["dup1"] = victimCallback`.
2. Before the DON responds, attacker (using their own arbitrary ECDSA key, thus a different `Sender`, but same `MessageId = "dup1"`) sends a second request. `HandleLegacyUserMessage` overwrites `savedCallbacks["dup1"] = attackerCallback` with no check that an entry already existed.
3. When the DON later replies to `dup1` (for the victim's original trigger the DON nodes were forwarded), `handleWebAPITriggerMessage` looks up `savedCallbacks["dup1"]`, finds the attacker's callback, and delivers the victim's response payload to the attacker via `savedCb.SendResponse(...)`.

There is no code path in `HandleLegacyUserMessage`, `Message.Validate`, or `gateway.ProcessRequest` that rejects duplicate/colliding `MessageId` values across different senders — only length and null-byte checks exist. The existing test suite explicitly documents this gap: `handler_test.go` only verifies that invalid messages don't leave stray entries, and the trailing TODO comment states sender/rate-limit validation is "pending question." [5](#0-4) 

### Impact Explanation
This is a cross-user response confusion / request impersonation issue: an unauthenticated party who can send signed gateway messages (any keyholder, since keys are self-generated and unauthenticated at this layer) can hijack another legitimate user's pending DON response by colliding on `MessageId`. Depending on what data flows through `web_api_trigger` responses (e.g., aggregated trigger results, execution status), this could leak the victim's trigger response contents to the attacker, or cause the victim's original callback to hang/never resolve (since the DON's response is delivered to the wrong callback, and `delete(h.savedCallbacks, msg.Body.MessageId)` removes the single map entry regardless of true owner) — resulting in denial of the victim's request and potential information disclosure to the attacker.

### Likelihood Explanation
Feasibility is high: `MessageId` is entirely attacker-chosen text (up to `MessageIdMaxLen` = 128 bytes), requiring no special credential beyond the ability to sign a message with any ECDSA key (self-generated, unauthenticated at the gateway user-message layer). The only timing requirement is that the attacker's colliding request arrive after the victim's registration but before the DON's response — a narrow window, but the messageId is guessable/predictable if callers use non-random IDs (e.g., sequential/increasing values), and no rate limit or allowlist currently blocks this specific race in `HandleLegacyUserMessage` (a TODO in the code explicitly flags missing allowlist/rate-limiting).

### Recommendation
Scope the `savedCallbacks` map key by both `Sender` and `MessageId` (e.g., `Sender + "|" + MessageId`), or reject registration outright if an entry already exists for that key with `if _, exists := h.savedCallbacks[key]; exists { return error }`. Additionally, validate on the `HandleNodeMessage` response path that the responding DON message's target/sender association matches the originally recorded requester, not just the raw `MessageId`.

### Proof of Concept
Go test plan (extends `core/services/gateway/handlers/capabilities/handler_test.go`):
```go
func TestHandler_MessageIdCollisionAcrossSenders(t *testing.T) {
    handler, _, don, nodes := setupHandler(t)
    ctx := t.Context()

    // Victim message with fixed MessageId "dup1", signed by nodes[0]-style victim key
    victimMsg := triggerRequestWithID(t, victimKey, "dup1", ...)
    victimCb := hc.NewCallback()
    don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)
    require.NoError(t, handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb))

    // Attacker crafts a different signer but reuses MessageId "dup1"
    attackerMsg := triggerRequestWithID(t, attackerKey, "dup1", ...)
    attackerCb := hc.NewCallback()
    require.NoError(t, handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb))

    // Assert: only one callback exists, and it now belongs to attacker (demonstrating the bug)
    handler.mu.Lock()
    require.Len(t, handler.savedCallbacks, 1)
    handler.mu.Unlock()

    // DON responds to "dup1" (representing the victim's original trigger)
    resp, err := hc.ValidatedResponseFromMessage(victimMsg)
    require.NoError(t, err)
    require.NoError(t, handler.HandleNodeMessage(ctx, resp, nodes[0].Address))

    // Attacker's callback receives the response meant for the victim
    r, err := attackerCb.Wait(t.Context())
    require.NoError(t, err) // demonstrates hijack: attacker got a response

    // Victim's callback never resolves (would time out)
    _, err = victimCb.Wait(shortTimeoutCtx)
    require.Error(t, err) // victim's callback starved
}
```
Expected (fixed) behavior: registration should fail or use a sender-scoped key so `attackerCb` never receives the victim's response and `victimCb` correctly resolves.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L339-366)
```go
	t.Run("savedCallbacks stored only when message is valid", func(t *testing.T) {
		require.Empty(t, handler.savedCallbacks)

		invalidPayloadMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidPayloadMsg, cb)
		require.NoError(t, err)
		_, _ = cb.Wait(t.Context())

		staleMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb2 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, staleMsg, cb2)
		require.NoError(t, err)
		_, _ = cb2.Wait(t.Context())

		badMethodMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb3 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, badMethodMsg, cb3)
		require.NoError(t, err)
		_, _ = cb3.Wait(t.Context())

		handler.mu.Lock()
		require.Empty(t, handler.savedCallbacks, "error paths must not leave entries in savedCallbacks")
		handler.mu.Unlock()
	})

	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```
