### Title
Cross-user response hijacking via `MessageId` collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores the response callback for a user's web-API-trigger request in `h.savedCallbacks` keyed solely by the attacker-controllable `msg.Body.MessageId`, with no binding to the original sender. Any unprivileged, signed client can submit a request reusing another user's in-flight `MessageId`, overwriting the victim's saved callback and causing the DON's eventual response to be delivered to the attacker instead of the victim.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes an incoming JSON-RPC request and, for legacy `DonId`-carrying messages, calls `h.HandleLegacyUserMessage(ctx, msg, callback)` after only validating message structure/signature via `msg.Validate()` [1](#0-0) . `Message.Validate()` checks signature format and recovers `Sender` from the signature, but places no constraint tying `MessageId` uniqueness to a particular sender — `MessageId` is fully attacker-chosen (any string ≤128 bytes not ending in a null byte) [2](#0-1) .

Inside `HandleLegacyUserMessage`, after payload/timestamp/method validation, the handler unconditionally overwrites the map entry:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [3](#0-2)  — there is no check for an existing entry, no check that a prior entry (if any) belongs to the same sender, and no rejection of collisions.

When a DON node later responds, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId` and delivers the response to whichever callback is currently stored:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
``` [4](#0-3) . `HandleNodeMessage` only verifies that the responding node's address matches `msg.Body.Sender` for the node-to-gateway leg [5](#0-4) ; it never re-checks that the callback bound to that `MessageId` still corresponds to the original requester.

Exploit flow: Victim V submits a signed request with `MessageId = X`, which is forwarded to DON members and awaits a response. Before the DON responds, attacker A submits their own validly signed request with the same `MessageId = X` (attacker only needs a valid signing key for their own message and knowledge/prediction of X — no privilege over V's session is required). A's callback overwrites V's entry in `h.savedCallbacks[X]`. When the DON node responds to the original request (which still carries `MessageId = X`), `handleWebAPITriggerMessage` delivers that response to A's callback, not V's. V's original HTTP call then times out on `callback.Wait(ctx)` (`core/services/gateway/gateway.go:278-285`), and A receives V's response payload — a cross-user response confusion / hijack, and simultaneously a denial-of-service to V.

The existing test suite confirms no such collision protection exists: it only verifies invalid/malformed messages don't leave stale entries, but never asserts rejection of a duplicate `MessageId` from a different sender [6](#0-5) .

### Impact Explanation
This allows an unauthenticated-relative-to-victim (but self-authenticated) attacker to hijack another user's web-API-trigger response, potentially exfiltrating data intended for the victim, and to deny service to the victim's request. This matches Chainlink's "cross-user response confusion" / request impersonation bounty impact class, since gateway responses are not partitioned per requester identity.

### Likelihood Explanation
Exploitation requires only: (1) ability to send a validly signed gateway message (any holder of a DON-member-recognized signing key, or any client whose signature satisfies `Validate()`), and (2) knowledge or prediction of a victim's in-flight `MessageId` within its short server-side window (bounded by DON round-trip time). If `MessageId`s are predictable (sequential counters, timestamps, or observable via client-side/browser instrumentation), a race is straightforward and repeatable; even blind guessing across a modest ID space is feasible given the generous 128-byte length allowance and lack of entropy requirements enforced by `Validate()`.

### Recommendation
Bind `savedCallbacks` entries to the originating sender (and/or DON) rather than `MessageId` alone — e.g., key by `(Sender, MessageId)` or store the sender alongside the callback and reject/ignore inserts on `MessageId` collision from a different sender, returning an error to the second requester instead of silently overwriting the first.

### Proof of Concept
Go test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build handler via `setupHandler(t)` as existing tests do.
2. Craft `msgVictim := triggerRequest(t, victimKey, ...)` with a fixed `MessageId` (e.g. by patching `triggerRequest`'s hardcoded `"12345"` or a new helper allowing explicit ID) and call `handler.HandleLegacyUserMessage(ctx, msgVictim, cbVictim)`.
3. Before delivering any node response, craft `msgAttacker` signed by a different key `attackerKey` but with the **same** `MessageId`, and call `handler.HandleLegacyUserMessage(ctx, msgAttacker, cbAttacker)`.
4. Assert (current buggy behavior) that `handler.savedCallbacks[MessageId].Callback == cbAttacker` (i.e., victim's callback was overwritten) — this demonstrates the vulnerability.
5. Simulate a node response for the original `MessageId` via `handler.HandleNodeMessage(...)` and assert that `cbAttacker.Wait(ctx)` receives the response while `cbVictim.Wait(ctx)` times out — proving response hijack/DoS.
6. A fixed implementation should instead have step 3 return an error (e.g., "message ID already in use by a different sender") and leave `cbVictim` as the bound callback, so the assertion in step 4 would need to change to `require.Error` on the attacker's `HandleLegacyUserMessage` call and `handler.savedCallbacks[MessageId].Callback == cbVictim`.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-256)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
	start := time.Now()
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
