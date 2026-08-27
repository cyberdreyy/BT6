### Title
Cross-user response hijacking via unauthenticated MessageId collision in `handleWebAPITriggerMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores the pending-request callback keyed solely by the client-supplied `msg.Body.MessageId` with no ownership binding to the caller/sender, and unconditionally overwrites any existing entry with the same key. Any signed gateway request can therefore hijack another in-flight request's response by reusing its `MessageId`, causing the DON's response for the victim's request to be delivered to the attacker instead.

### Finding Description
`api.Message.Body.MessageId` is a client-supplied string, only length/format-validated in `Message.Validate()` (non-empty, ≤128 bytes, no trailing NUL) [1](#0-0) , with no uniqueness or ownership check tied to the message signer. The gateway's `ProcessRequest` validates the message and hands it, unmodified, to `HandleLegacyUserMessage` via the (multi)handler [2](#0-1) .

In `HandleLegacyUserMessage`, the callback is saved keyed purely by `msg.Body.MessageId`: [3](#0-2) 
This is a plain map write (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) with no check for an existing entry, no comparison of `msg.Body.Sender`, and no rejection on collision — a second `HandleLegacyUserMessage` call reusing the same `MessageId` silently replaces the first caller's saved callback.

Later, when the DON responds, `handleWebAPITriggerMessage` looks the callback up strictly by `MessageId` and invokes `SendResponse` on whatever callback is currently stored under that key: [4](#0-3) 

Because the lookup/delivery path has no binding to the original sender, if an attacker submits a second `HandleLegacyUserMessage` request with the same `MessageId` as a victim's in-flight request before the DON responds, the attacker's callback overwrites the victim's in the map. When the DON's response for the victim's original request arrives (matched only by `MessageId`, and the node-side `Sender == nodeAddr` check in `HandleNodeMessage` only validates the DON node, not which end-user callback it should map to [5](#0-4) ), the response is delivered to the attacker's callback via `savedCb.SendResponse(...)`, redirecting the victim's response channel to the attacker.

No allowlist or rate limiting currently guards this path — the code even has a `// TODO: apply allowlist and rate-limiting here` comment right before the vulnerable write [6](#0-5) , confirming this gap is unmitigated.

### Impact Explanation
This allows any holder of a valid signing key reachable via `HandleLegacyUserMessage` (unprivileged relative to other users — no special role required) to redirect another user's DON trigger response to themselves, i.e., cross-user response confusion / impersonation of the response channel. Depending on the payload content (e.g., HTTP trigger execution results, potentially containing data intended only for the original caller), this is an information-disclosure and request-hijacking issue affecting request/response integrity between the gateway and its callers.

### Likelihood Explanation
Exploitation requires only: (1) the ability to sign an `api.Message` (any valid gateway signing key, not privileged), and (2) knowledge or guessability of an in-flight `MessageId` used by another caller, plus a race to submit before the legitimate response arrives. If client libraries use predictable/sequential/shared MessageId schemes, this is straightforward and repeatable; even with random IDs, an attacker controlling their own request timing/traffic could probe overlapping windows. No signature forgery or credential theft of the victim is needed.

### Recommendation
Bind saved callbacks to the authenticated sender, not just the raw `MessageId` — e.g., key `savedCallbacks` by a composite of `(msg.Body.Sender, msg.Body.MessageId)`, or reject `HandleLegacyUserMessage` calls whose `MessageId` already has a live, unexpired entry unless the sender matches. Additionally, verify `msg.Body.Sender` (extracted from the signature in `Validate()`) is recorded in `savedCallback` and cross-checked at response-delivery time before invoking `SendResponse`.

### Proof of Concept
Go handler-level integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler with a mock `handlers.DON` that records `SendToNode` calls.
2. Create two `handlers.Callback` mocks: `victimCb` and `attackerCb`.
3. Call `h.HandleLegacyUserMessage(ctx, victimMsg, victimCb)` where `victimMsg.Body.MessageId = "shared-id"`, signed with victim's key.
4. Before any node response arrives, call `h.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)` with the same `MessageId = "shared-id"`, signed with attacker's key.
5. Assert `h.savedCallbacks["shared-id"].Callback == attackerCb` (victim's callback silently evicted).
6. Simulate the DON responding to the original request via `h.HandleNodeMessage(ctx, respMatchingSharedId, nodeAddr)`.
7. Assert `attackerCb.SendResponse` was invoked with the victim's response payload, and `victimCb.SendResponse` was never called — demonstrating the response meant for the victim was delivered to the attacker.

### Citations

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
