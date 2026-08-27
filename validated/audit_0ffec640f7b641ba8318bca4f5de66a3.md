### Title
Attacker-controlled `MessageId` collision allows cross-user callback overwrite and response hijacking - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores a caller's callback in the shared `h.savedCallbacks` map keyed solely by the client-supplied `msg.Body.MessageId`, with no per-sender namespacing or collision check. Since `MessageId` is chosen entirely by the requester and only checked for length/null-suffix by `Message.Validate()`, two independent, unauthenticated users can register requests with identical `MessageId`s, causing the second request to silently overwrite the first's saved callback and letting `handleWebAPITriggerMessage` deliver a node response to the wrong requester.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:411-414) executes:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
There is no check whether `msg.Body.MessageId` already exists in the map before overwriting, and no binding of the map key to the requester's sender/signature. `msg.Body.MessageId` is fully attacker-controlled: `Message.Validate()` (core/services/gateway/api/message.go:54-88) only enforces length bounds (`MessageIdMaxLen`) and a null-byte-suffix check — it never enforces uniqueness or ties the ID to the caller's identity beyond signing over the ID (which only proves the sender chose that ID, not that it's unique).

Exploit flow:
1. Victim calls `gateway.ProcessRequest` → `HandleLegacyUserMessage` with `MessageId = "X"`. Their `callback` is stored at `h.savedCallbacks["X"]` and the request forwarded to all DON members via `don.SendToNode`.
2. Before the DON nodes respond, an unauthenticated attacker submits a second legacy trigger request with the same `MessageId = "X"` (a valid signature over an attacker-chosen ID is trivial to produce with any key). This overwrites `h.savedCallbacks["X"]` with the attacker's own `callback`, and the victim's `savedCallback` reference is now unreachable from the map (though the victim's own `callback.Wait(ctx)` is still blocked waiting).
3. When a DON node responds to the *victim's* original request with `MessageId = "X"`, `handleWebAPITriggerMessage` (handler.go:148-162) does:
   ```go
   savedCb, found := h.savedCallbacks[msg.Body.MessageId]
   delete(h.savedCallbacks, msg.Body.MessageId)
   ...
   return savedCb.SendResponse(...)
   ```
   Since the map now holds the attacker's callback under key "X", the victim's response payload (which may contain sensitive trigger output data) is delivered to the attacker instead of the victim.

No signature/sender check ties the stored callback to the specific message occurrence, and no allowlist/uniqueness enforcement exists prior to the write at line 412, so this path is fully reachable by an unauthenticated gateway client.

### Impact Explanation
This is cross-user response confusion / hijacked capability execution result: an unprivileged, unauthenticated attacker can cause another user's DON-produced response (potentially containing sensitive data returned by the workflow trigger) to be delivered to the attacker instead of the legitimate requester, while the legitimate requester's request either times out or receives no/incorrect response.

### Likelihood Explanation
The only precondition is that the attacker can guess or match a victim's in-flight `MessageId` and submit a second `HandleLegacyUserMessage` request before the DON responds — no authentication, allowlist membership, or special role is required to reach `gateway.ProcessRequest` → `HandleLegacyUserMessage`. If `MessageId`s are predictable (e.g., sequential, timestamp-based, or the attacker can observe pending IDs, or simply chooses a commonly-used fixed value), the race is straightforward and repeatable given the 2-minute callback window (`defaultCallbackMaxAgeSec`).

### Recommendation
Reject registration of a new callback if `msg.Body.MessageId` already exists in `h.savedCallbacks` (return an error to the second requester instead of overwriting), and/or scope the map key to include the sender address (e.g., `sender+messageId`) so that collisions across different senders cannot occur.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct `handler` via `NewHandler`.
2. Call `h.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` with `victimMsg.Body.MessageId = "X"` signed by victim key.
3. Before delivering a node response, call `h.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` with `attackerMsg.Body.MessageId = "X"` signed by a different (attacker) key.
4. Assert `h.savedCallbacks["X"].Callback == attackerCallback` (overwrite occurred) rather than being rejected.
5. Deliver a `HandleNodeMessage` response with `MessageId = "X"` (simulating the node's response to the victim's original request) and assert that `attackerCallback.SendResponse` is invoked instead of `victimCallback.SendResponse`, demonstrating the victim's response was delivered to the attacker. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L410-420)
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
