### Title
Cross-tenant WebAPI trigger response hijack via unauthenticated MessageId collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores a pending requester's `handlers.Callback` in `h.savedCallbacks` keyed solely by the caller-supplied `msg.Body.MessageId`, with no check for an existing/in-flight entry and no binding to the message signer. Because `MessageId` is an arbitrary, signer-independent field chosen entirely by the requester, an unprivileged attacker (any holder of a valid signing key who can send signed gateway requests) can submit a second legacy request using the same `MessageId` as another tenant's in-flight `web_api_trigger` request, overwriting the victim's callback entry. When the DON node later responds with that `MessageId`, `handleWebAPITriggerMessage` delivers the victim's trigger response to the attacker's callback instead of the victim's.

### Finding Description
- `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`) validates the message's signature/timestamp/method but never checks whether `msg.Body.MessageId` already has a `savedCallback` registered. It unconditionally does:
```go
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
``` [1](#0-0) 
- `MessageId` is fully attacker-controlled input (`api.MessageBody.MessageId`), and `Message.Validate()` only checks length/null-byte constraints on it — it is not derived from or bound to the signer/sender identity. [2](#0-1) 
- The response-delivery path, `handleWebAPITriggerMessage`, looks up and deletes the callback purely by `MessageId` and sends the response to whichever callback is currently stored, with no verification that the callback belongs to the sender who originated the corresponding request:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
``` [3](#0-2) 
- `HandleNodeMessage` only validates that the responding node (`nodeAddr`) matches `msg.Body.Sender` (the DON node), which is unrelated to end-user identity and does not protect against this collision: [4](#0-3) 
- Exploit flow: (1) Victim submits a legit signed `web_api_trigger` request with `MessageId = "X"`; gateway stores `savedCallbacks["X"] = victimCallback` and forwards to DON nodes. (2) Before the DON responds, attacker (any other signer) submits their own signed `web_api_trigger` request also using `MessageId = "X"` (chosen/predicted/brute-forced by the attacker — nothing prevents any value). This overwrites `savedCallbacks["X"] = attackerCallback`. (3) When the DON node's response for the victim's original request arrives keyed by `MessageId = "X"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds the attacker's callback, and delivers the victim's trigger response content to the attacker. The victim's own callback is orphaned/never resolved (eventually times out).
- Existing checks (signature validation, staleness check, method check) all pass for the attacker's own message since they only validate that message's own signature — they do nothing to prevent a duplicate `MessageId` from a different signer.

### Impact Explanation
This breaks the "one authenticated sender per gateway request" invariant: a completely unrelated, unprivileged signer can redirect and read the response payload intended for another user's `web_api_trigger` request (cross-user response confusion / request impersonation). Depending on what data flows back through `web_api_trigger` responses (workflow trigger event data/results), this can leak another tenant's data to the attacker and deny the victim their response (denial of service on their request), corresponding to the Chainlink bounty impact class of unauthorized access to another user's data / request hijacking.

### Likelihood Explanation
The only precondition is possessing any valid signing key capable of producing a validly-signed gateway `Message` (i.e., "any address sending signed gateway requests" — explicitly an in-scope unprivileged attacker) and being able to send it to the same DON/handler within the victim's request's in-flight window (bounded by `CallbackMaxAgeSec`, default 120s). No prediction of cryptographic secrets is required — the attacker just needs to guess/observe/reuse the same `MessageId` string, which is a plain user-chosen field with no entropy requirement enforced by `Validate()`. This is fully reproducible with a unit test and requires no privileged access.

### Recommendation
Bind `savedCallbacks` entries to the request's authenticated sender in addition to `MessageId` (e.g., key by `(sender, MessageId)` tuple), and/or reject `HandleLegacyUserMessage` calls that attempt to reuse a `MessageId` already present in `savedCallbacks` (return an error instead of silently overwriting). Additionally, verify in `handleWebAPITriggerMessage`/`HandleNodeMessage` that the node's response corresponds to a request actually sent to that node for that `MessageId`/sender pair before dispatching the callback.

### Proof of Concept
Extend `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Use `setupHandler(t)` to get `handler`, `don` mock, and `nodes`.
2. Build `victimMsg := triggerRequest(t, victimKey, ...)` with `MessageId = "X"` and call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`; confirm `handler.savedCallbacks["X"]` is the victim's callback.
3. Build `attackerMsg` signed with a different key but with the same `MessageId = "X"` (bypass the hardcoded `"12345"` in the test helper by setting `msg.Body.MessageId = "X"` explicitly before `Sign`/`Validate`), and call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)`.
4. Assert `handler.savedCallbacks["X"]` now equals `attackerCb`, not `victimCb`.
5. Simulate the DON node responding to the victim's original request via `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)` using a response correlated to `MessageId = "X"`.
6. Assert that `attackerCb.Wait(ctx)` returns the response payload (hijack succeeded) while `victimCb.Wait(ctx)` times out/never resolves — demonstrating the cross-tenant response hijack.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```
