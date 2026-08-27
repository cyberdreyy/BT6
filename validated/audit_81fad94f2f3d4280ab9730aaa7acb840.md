### Title
Attacker-controlled `MessageId` collisions allow silent overwrite/hijack of another user's callback in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores the caller-supplied `callback` in `h.savedCallbacks` keyed only by the client-controlled `msg.Body.MessageId`, with no check for an existing/in-flight entry before overwriting it. Because `MessageId` is fully attacker-chosen (any string up to 128 bytes, not required to be unique per requester), a second unrelated request that reuses the same `MessageId` silently replaces the first requester's pending callback, so the eventual node response for that `MessageId` is delivered to whichever caller most recently registered.

### Finding Description
`MessageId` originates purely from client input: in `JsonRPCCodec.DecodeJSONRequest` it is copied straight from the JSON-RPC request `ID` [1](#0-0) , and `Message.Validate()` only checks its length/suffix, not uniqueness or binding to a particular sender identity beyond what's covered by the signature over that same value [2](#0-1) . Two different callers (different signing keys) can each independently choose the identical `MessageId` string in their own signed messages — nothing prevents this since the signature only proves the sender authored that specific message, not that the ID is exclusive to them.

In `HandleLegacyUserMessage`, the handler stores the callback with an unconditional map write: [3](#0-2) 
There is no `if _, exists := h.savedCallbacks[msg.Body.MessageId]; exists { ... }` guard. If requester A submits a trigger message with `MessageId = "X"`, then requester B submits another message reusing `MessageId = "X"` before A's DON response arrives, B's callback overwrites A's entry in the map.

When any node later responds to the trigger method, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId` and invokes whichever callback is currently registered, without any binding to the original sender/session: [4](#0-3) 
Thus if A's DON response arrives after B has overwritten the map entry, A's node response is delivered to B's callback instead of A's (cross-user delivery), while A's original callback is silently dropped/orphaned and will eventually time out with `RequestTimeoutError` in the gateway's `callback.Wait(ctx)` path [5](#0-4) . No allowlist or rate limiting currently exists at this point to prevent MessageId reuse — the code even has a `// TODO: apply allowlist and rate-limiting here` comment immediately above the request-forwarding logic [6](#0-5) .

### Impact Explanation
This is a cross-user response confusion bug: an unprivileged attacker who submits a legacy trigger message reusing a `MessageId` already in flight for another user can cause that other user's DON acknowledgment/response to be delivered to the attacker's own callback (HTTP connection) instead, while the legitimate user's request silently times out. The data returned via `codec.EncodeLegacyResponse(msg)` for `MethodWebAPITrigger` is the node's trigger acknowledgment content, not the underlying HTTP target/action response body (that path uses `handleWebAPIOutgoingMessage`, which does not consult `savedCallbacks`), so the practical data-exposure impact is limited to trigger-ack content rather than arbitrary HTTP response bodies/headers as hypothesized in the question. Still, it violates response isolation between users and enables a denial-of-service against the legitimate requester (dropped/timed-out response).

### Likelihood Explanation
Exploitation only requires unauthenticated/unprivileged ability to submit legacy trigger messages to the gateway with an attacker-chosen `MessageId`, and timing the second submission to land before the first requester's DON response is processed. `MaxSavedCallbacks`/pruning do not prevent this since the issue is a direct overwrite, not eviction from map growth. This is straightforward to reproduce and repeat.

### Recommendation
Reject or safely handle `MessageId` collisions in `HandleLegacyUserMessage`: check `h.savedCallbacks` for an existing entry before storing, and either return an error to the second caller (e.g., "duplicate/in-flight message id") or scope the key by `(sender, MessageId)` instead of `MessageId` alone so that requests from different signers can never collide.

### Proof of Concept
Go handler-level test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build two triggerRequest messages (`triggerRequest` helper already used for these) signed by two different node/user keys but with the same `Body.MessageId` (e.g., `"dup-id"`).
2. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` then immediately `handler.HandleLegacyUserMessage(ctx, msgB, cbB)` before either resolves.
3. Assert `h.savedCallbacks["dup-id"]` now equals `cbB`'s wrapper, not `cbA`'s (via reflection/unexported field access or an exported test hook).
4. Simulate a node response for `msgA`'s content via `handler.HandleNodeMessage(...)` and assert that `cbB.Wait(ctx)` (not `cbA.Wait(ctx)`) receives the response, while `cbA.Wait(ctx)` times out — demonstrating the cross-user delivery and the silent drop of A's original callback.

### Citations

**File:** core/services/gateway/api/jsonrpccodec.go (L24-33)
```go
func (*JsonRPCCodec) DecodeJSONRequest(request jsonrpc2.Request[json.RawMessage]) (*Message, error) {
	var msg Message
	err := json.Unmarshal(*request.Params, &msg)
	if err != nil {
		return nil, err
	}
	msg.Body.MessageId = request.ID
	msg.Body.Method = request.Method
	return &msg, nil
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
