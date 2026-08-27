### Title
Response Hijack / Callback Orphaning via Unkeyed `MessageId` Collision in `savedCallbacks` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores each in-flight callback in `h.savedCallbacks` keyed only by `msg.Body.MessageId`, with no check for an existing entry and no binding to the requester's identity (`Sender`). This differs from `common.requestCache`, which keys pending requests on `(sender, messageId)` and explicitly rejects duplicates with `"request already exists"`. As a result, any second legacy request that reuses an in-flight `MessageId` — even from a different sender — silently overwrites the first callback, orphaning the original caller and redirecting the eventual DON node response to the attacker's callback.

### Finding Description
`HandleLegacyUserMessage` unconditionally assigns: [1](#0-0) 
without checking `found` first, unlike `common.requestCache.NewRequest`, which uses a composite key `globalId{sender, id}` and returns an error if an entry already exists: [2](#0-1) 

Critically, the `savedCallbacks` map key is `msg.Body.MessageId` alone — it does not include `msg.Body.Sender` — even though `Sender` is available and set during `msg.Validate()`: [3](#0-2) 

The `MessageId` itself is fully attacker-controlled (any string ≤128 bytes, validated only for length/null-suffix), and is part of the signed payload but not required to be unique or session-bound: [4](#0-3) 

Request flow: the gateway's `ProcessRequest` validates the message and forwards it to `HandleLegacyUserMessage`: [5](#0-4) 

When the DON node eventually responds, `handleWebAPITriggerMessage` looks up and deletes the callback solely by `MessageId`, with no sender/receiver binding check, and delivers the response to whichever callback is currently stored: [6](#0-5) 

Exploit flow: Victim sends a legitimate signed request with `MessageId = X`, which is stored in `savedCallbacks["X"]` bound to `callback1`. Before the DON responds, attacker sends their own signed request (with a different `Sender`, since messages must be self-signed) but reusing the same `MessageId = X`. Since there is no existing-key check, `savedCallbacks["X"]` is overwritten with `callback2`. When the DON node's response for the victim's original request eventually arrives, `handleWebAPITriggerMessage` looks it up by `MessageId` only, finds `callback2`, and delivers the victim's response payload to the attacker instead of the original caller — the victim's channel is orphaned and never resolved (eventually times out at the gateway `callback.Wait` layer).

Because `MessageId` is fully attacker-chosen and there is no per-sender partitioning of the map (unlike `requestCache`), the attack does not require guessing a truly secret value in the strict sense if IDs are reused/predictable/short-lived in a given deployment's client pattern, but in general this requires knowledge of an in-flight victim `MessageId`. The self-collision variant (attacker races themselves across two of their own sessions) is trivially reproducible and demonstrates the orphaning/DoS half of the bug deterministically without needing to guess anything.

### Impact Explanation
This is a cross-user response/request confusion vulnerability: an attacker-controlled callback can receive a response payload intended for a different user's in-flight request, while the legitimate caller's channel is silently orphaned until gateway-level timeout. This matches the "cross-user response confusion" and "request impersonation-adjacent" impact class in scope. The response is still a "bound-to-node" response, so no secrets beyond what that specific webhook/trigger response contains would be disclosed, but it is a concrete confidentiality/availability violation of request isolation between users of the gateway/WebAPI trigger handler.

### Likelihood Explanation
Requires the attacker to submit a signed message with a `MessageId` value that collides with another party's in-flight request. Self-collision (two sessions belonging to the same attacker) is trivial and fully attacker-controlled, proving the underlying flaw (missing existence check, missing sender-scoping) deterministically. Cross-user collision additionally requires knowledge of a victim's in-flight `MessageId`, which is only feasible if `MessageId`s are predictable/short/reused by legitimate clients or somehow observable by the attacker (e.g., echoed elsewhere, low entropy, or reused per session by a specific integration). No privileged access is needed — any unauthenticated/API-token holder capable of sending gateway legacy messages can attempt this.

### Recommendation
Scope `savedCallbacks` keys by `(Sender, MessageId)` rather than `MessageId` alone (mirroring `common.requestCache`'s `globalId{sender, id}` pattern), and reject/overwrite-guard duplicate keys the same way `requestCache.NewRequest` does (return `"request already exists"` instead of silently overwriting). Apply the same sender-bound key when looking up and deleting the callback in `handleWebAPITriggerMessage`/`HandleNodeMessage` to ensure a node's response can only be routed back to the callback of the request that actually produced it.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build `msg1` signed by `nodes[0]`-independent user key A with `MessageId = "dup-id"`, call `handler.HandleLegacyUserMessage(ctx, msg1, cb1)`. Assert `handler.savedCallbacks["dup-id"]` is bound to `cb1`.
2. Build `msg2` signed by a different user key B, reusing `MessageId = "dup-id"`, call `handler.HandleLegacyUserMessage(ctx, msg2, cb2)`. Assert (bug) that `handler.savedCallbacks["dup-id"]` is now bound to `cb2`, overwriting `cb1` with no error returned.
3. Simulate the DON node response for `msg1` (`hc.ValidatedResponseFromMessage(msg1)`) via `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
4. Assert `cb2.Wait(ctx)` returns the response payload derived from `msg1` (hijack), and assert `cb1.Wait(ctx)` times out / never resolves (orphaned), i.e. `cb1.SendResponse` is never invoked while `cb2.SendResponse` is invoked with data belonging to the other session's request.

### Citations

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

**File:** core/services/gateway/handlers/common/requestcache.go (L50-63)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```

**File:** core/services/gateway/api/message.go (L54-67)
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
```

**File:** core/services/gateway/api/message.go (L82-87)
```go
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
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
