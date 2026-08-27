### Title
Cross-user response redirection via unauthenticated `MessageId` collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores the caller's response callback in `h.savedCallbacks` keyed solely by the attacker-controlled `msg.Body.MessageId`, with no existence check and no scoping by sender or DON. A second request that reuses the same `MessageId` (even from a completely different signer) silently overwrites the first request's callback entry, so the eventual node response for the victim's request is delivered to the attacker's callback instead, and the victim's HTTP call hangs until timeout.

### Finding Description
`MessageId` is a fully client-controlled, unauthenticated field: it only needs to satisfy length/format checks in `Message.Validate()` (non-empty, ≤128 bytes, no trailing null byte) — [1](#0-0) . There is no server-side uniqueness enforcement, randomness requirement, or per-sender namespace tied to it.

When a user request reaches `gateway.ProcessRequest`, it is validated and forwarded straight to `h.HandleLegacyUserMessage`, and any authenticated signer can freely choose the same `MessageId` string as another signer's in-flight request: [2](#0-1) .

Inside `HandleLegacyUserMessage`, the per-request `Callback` is stored keyed only by `msg.Body.MessageId`, unconditionally overwriting any pre-existing entry for that key: [3](#0-2) 

Later, when a DON node responds, `handleWebAPITriggerMessage` looks up and deletes the callback purely by `MessageId` and delivers the (attacker-overwritten) response there: [4](#0-3) 

If Attacker submits `MessageB{message_id:"X", sender: attacker}` after Victim's `MessageA{message_id:"X", sender: victim}` was registered but before the DON node's response for `MessageA` arrives, `h.savedCallbacks["X"]` now points to the attacker's callback. When the node eventually responds for the victim's original trigger request (same `MessageId` "X" is echoed by nodes since it's part of the signed request forwarded to them), `handleWebAPITriggerMessage` will hand that response to the attacker's callback, and the victim's own callback object is orphaned — the victim's synchronous `callback.Wait(ctx)` in `gateway.ProcessRequest` will simply time out: [5](#0-4) .

The identical unscoped-map pattern exists in the dummy handler as well: [6](#0-5) .

The existing test suite only verifies that invalid/error paths don't leave dangling entries; it does not test for cross-sender `MessageId` collisions: [7](#0-6) .

### Impact Explanation
This is a cross-user response confusion / isolation-violation bug: an unprivileged user (any address able to sign and submit a gateway request) can cause another user's legitimate capability-trigger response to be delivered to the attacker instead of the victim, and the victim's request silently fails (times out) with no response ever received. This matches the "cross-user response confusion" impact class called out in scope — no capability execution result leaks in this case, but the *victim's* trigger result is redirected to the attacker's session, and the victim is denied their own response.

### Likelihood Explanation
Preconditions: attacker must (a) be able to submit signed capability trigger requests to the gateway (this only requires an EOA private key, no special DON/gateway privilege), and (b) know or successfully guess/race the exact `MessageId` string used by the victim's concurrent request. Because `MessageId` values are frequently short, deterministic, or predictable in client implementations (e.g., incrementing counters or fixed values as seen in the test helper's use of `"12345"`), and because there is no collision-rejection logic at all in the handler, this is straightforward to trigger deliberately once an attacker can predict/observe an in-flight `MessageId`, and it can also occur accidentally between two well-behaved but colliding clients.

### Recommendation
Namespace `savedCallbacks` (and the equivalent map in `handler.dummy.go`) by `(sender, DonId, MessageId)` instead of `MessageId` alone, since `msg.Body.Sender` is already derived from the verified signature in `Message.Validate()`. Additionally, reject (rather than silently overwrite) any `HandleLegacyUserMessage` call whose composite key already has a live, non-expired entry, returning an explicit "duplicate message ID" error to the caller.

### Proof of Concept
Go test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build two valid signed `triggerRequest` messages with two different node/user private keys but the same hardcoded `message_id` (e.g., reuse the existing `messageID := "12345"` constant already used by the test helper, or supply an explicit identical value for two different signer keys).
2. Call `handler.HandleLegacyUserMessage(ctx, msgVictim, cbVictim)`, then before the victim's node response arrives, call `handler.HandleLegacyUserMessage(ctx, msgAttacker, cbAttacker)`.
3. Assert `handler.savedCallbacks["12345"].Callback == cbAttacker` (i.e., victim's callback got overwritten) — this demonstrates the collision.
4. Simulate the node responding to the original (victim) message via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`, and assert that `cbAttacker.Wait(...)` receives the response while `cbVictim.Wait(...)` times out — proving cross-user response delivery.
5. Expected (fixed) behavior: the second `HandleLegacyUserMessage` call should either be rejected with a duplicate-ID error, or be stored under a distinct key so that `cbVictim` still receives its own correct response and `cbAttacker` cannot intercept it.

### Citations

**File:** core/services/gateway/api/message.go (L54-66)
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

**File:** core/services/gateway/gateway.go (L278-285)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-109)
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

func (d *dummyHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	var msg api.Message
	err := json.Unmarshal(*resp.Result, &msg)
	if err != nil {
		return err
	}
	msg.Body.MessageId = resp.ID
	err = msg.Validate()
	if err != nil {
		return err
	}
	if nodeAddr != msg.Body.Sender {
		return fmt.Errorf("node address %s does not match message sender %s", nodeAddr, msg.Body.Sender)
	}
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(&msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L339-363)
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
```
