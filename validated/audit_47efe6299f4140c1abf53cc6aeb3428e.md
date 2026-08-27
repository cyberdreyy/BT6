### Title
MessageId is fully attacker-controlled and unverified for uniqueness, allowing savedCallbacks collision/overwrite and cross-user response misdelivery in the web-api-capabilities gateway handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`Message.Validate()` [1](#0-0)  only checks that `MessageId` is non-empty, ≤128 bytes, and does not end with a null byte — there is no uniqueness, format, or entropy requirement enforced anywhere in the codebase. `gateway.ProcessRequest` calls `msg.Validate()` before dispatching a legacy request to a handler [2](#0-1) , so the "empty MessageId" edge case described in the prompt is already blocked — but nothing stops an attacker from choosing any fixed, guessable, non-empty `MessageId` (e.g. `"12345"`, a value copied from a script/sample client such as `core/scripts/gateway/web_api_trigger/invoke_trigger.go` which defaults `messageID` to `"12345"` [3](#0-2) ).

In `handler.HandleLegacyUserMessage`, after validating the payload/method, the handler stores the caller's callback keyed only by `msg.Body.MessageId`, with an unconditional map write that silently overwrites any pre-existing entry for that ID: [4](#0-3) 

The request is then fanned out to all DON members using that same ID as the JSON-RPC correlation ID [5](#0-4) .

When a node response arrives, `handleWebAPITriggerMessage` looks the callback up **purely by `MessageId`**, deletes it, and delivers the (node-supplied) response body to whichever callback is currently registered under that ID — the first node response wins and any others are dropped: [6](#0-5) 

There is no check that the caller who registered the callback is the same requester whose message produced that particular node response, no per-request nonce/session binding, and no rejection of an already-in-use `MessageId`. If two unprivileged callers (a victim and an attacker) submit legacy requests with the same `MessageId` to the same DON around the same time, the second `HandleLegacyUserMessage` call silently overwrites the first callback entry. Consequently:
- The original caller's callback is orphaned and will only ever see a client-side timeout (`callback.Wait` in `gateway.ProcessRequest`), a request-starvation/DoS effect.
- Whichever request's callback remains registered at the time a node response for that `MessageId` arrives receives the payload — enabling response misdelivery/cross-user confusion when the underlying node round-trip timing allows the "wrong" registrant to be attached to a given ID.

### Impact Explanation
This is a request-correlation integrity flaw in a component that is reachable by any unauthenticated/unprivileged gateway API caller (no operator/admin/DON compromise required). Concrete impacts:
- **Denial of service** against a specific victim's request by ID-squatting (attacker submits the same, guessable `MessageId` the victim is expected/likely to use), causing the victim's legitimate request to silently hang/time out.
- **Cross-user response confusion**: under the map-overwrite race, a response destined for one caller's request can be delivered to a different caller's callback because delivery is keyed solely by attacker-chosen `MessageId`, with no requester/session binding.

This matches the "cross-user response confusion" / "unauthorized action affecting another user's request" bounty class, though the practical severity is bounded by the fact that message payloads here are workflow-trigger echo/ACK-style responses (not secrets), and it is a race/collision-dependent condition rather than deterministic impersonation.

### Likelihood Explanation
No credential or elevated role is needed — any client able to reach the gateway's legacy JSON-RPC endpoint can submit a signed message with an arbitrary `MessageId` of their choosing (only signature validity, length, and non-null-suffix are enforced). Exploitation requires the attacker to guess/predict or already know a `MessageId` a victim is using concurrently (feasible where clients use fixed/sequential/predictable IDs, as seen in this repo's own sample/test clients defaulting to `"12345"`), plus winning a timing race on the map write/read. This makes it readily repeatable in test/dev environments using predictable IDs, but less reliable against clients using high-entropy random IDs, since nothing in the system enforces ID randomness either way.

### Recommendation
- Enforce `MessageId` uniqueness at registration time in `HandleLegacyUserMessage`: reject (or namespace) the request if an entry for that ID already exists in `savedCallbacks` rather than silently overwriting it.
- Bind the saved callback to a gateway-generated, unpredictable internal correlation key (in addition to/independent of the client-supplied `MessageId`), so client-chosen IDs cannot cause cross-request collisions.
- Optionally require/derive `MessageId` uniqueness scoped per-sender (e.g., `sender+MessageId`) to prevent one sender's collisions from affecting another sender's in-flight requests.

### Proof of Concept
Go unit test targeting `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Call `setupHandler(t)` to obtain `handler`, `don`, and `nodes`.
2. Build two distinct `triggerRequest` messages ("victim" and "attacker") signed by different keys but with the **same** `MessageId` (e.g. `"12345"`) and distinct payload timestamps/params.
3. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`; assert `handler.savedCallbacks["12345"]` is the victim's callback.
4. Call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)`; assert `handler.savedCallbacks["12345"]` is now the **attacker's** callback (victim's entry was overwritten) — demonstrating the missing uniqueness check.
5. Simulate a node response for `MessageId "12345"` carrying the victim's expected payload via `handler.HandleNodeMessage(...)`.
6. Assert that `attackerCb.Wait(ctx)` receives the response (containing data correlated with the victim's original request) while `victimCb.Wait(ctx)` times out — proving cross-registrant response delivery caused solely by attacker-chosen, non-unique `MessageId`.
7. As a control, add a `TestMessage_Validate` case confirming `api.Message.Validate()` has no uniqueness/format check beyond length/null-suffix (already visible in `core/services/gateway/api/message_test.go`), documenting the absence of any entropy requirement.

### Citations

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

**File:** core/scripts/gateway/web_api_trigger/invoke_trigger.go (L56-56)
```go
	messageID := flag.String("id", "12345", "Request ID")
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

**File:** core/services/gateway/handlers/common/message_util.go (L82-105)
```go
// ValidatedRequestFromMessage converts a legacy Gateway Message to a JSON-RPC request
func ValidatedRequestFromMessage(msg *api.Message) (*jsonrpc.Request[json.RawMessage], error) {
	if msg == nil {
		return nil, errors.New("nil message")
	}
	if msg.Body.MessageId == "" {
		return nil, errors.New("message ID is empty")
	}
	if msg.Body.Method == "" {
		return nil, errors.New("method is empty")
	}
	params, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal message: %w", err)
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	return req, nil
}
```
