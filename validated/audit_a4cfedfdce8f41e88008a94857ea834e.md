### Title
Cross-user response hijacking via `MessageId` collision in `handler.savedCallbacks` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores the pending user callback keyed purely by the attacker-controlled `msg.Body.MessageId`, and `handleWebAPITriggerMessage`/`HandleNodeMessage` deliver whichever DON response arrives for that `MessageId` to whatever callback currently occupies that map slot. Because the `MessageId` is fully client-supplied and unvalidated for uniqueness, an unauthenticated gateway user can submit a request whose `MessageId` collides with a concurrently in-flight legitimate request, overwrite that request's saved callback, and receive the victim's DON response instead of the victim.

### Finding Description
The gateway derives `msg.Body.MessageId` directly from the caller-supplied JSON-RPC `id` field (see `ProcessRequest` in `core/services/gateway/gateway.go:218-262`, and `Message.Validate()` in `core/services/gateway/api/message.go:54-67`, which only checks length/format, not uniqueness or ownership). Any unauthenticated caller of the `/user` gateway endpoint can pick any `MessageId` they want.

`HandleLegacyUserMessage` then stores the callback keyed only by this attacker-chosen string: [1](#0-0) 

If a second request (from a different sender/attacker) arrives with the same `MessageId` while the first request is still pending (no node has answered yet), the map entry is silently overwritten — there is no uniqueness check on `MessageId` before this assignment (unlike, e.g., `httpTriggerHandler.setupCallback` in the v2 handler, which does check `if _, found := h.callbacks[requestID]; found { ... }`, at `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go:402-405`; the legacy `capabilities/handler.go` path has no such guard).

When a DON node later responds to the *original* (victim) request, `HandleNodeMessage` only checks that the response sender matches the node address that delivered it: [2](#0-1) 

It performs no check that the response corresponds to the same requester/session that originally created the `MessageId`. `handleWebAPITriggerMessage` then looks the callback up purely by `MessageId` and delivers the raw response to it: [3](#0-2) 

Since the attacker's later request overwrote `savedCallbacks[MessageId]`, the victim's DON response (containing the victim's trigger response payload) is delivered to the attacker's callback instead of the victim's. The victim's original callback is orphaned and will simply time out. Note `HandleLegacyUserMessage` also has a `TODO: apply allowlist and rate-limiting here` (`handler.go:384`), confirming no rate limiting currently constrains rapid submission of colliding `MessageId`s by an attacker racing a target request.

### Impact Explanation
This is a cross-user response confusion vulnerability: an unprivileged/unauthenticated gateway user can cause another user's DON/trigger response (which may contain sensitive payload data returned by the node) to be delivered to the attacker instead of the legitimate requester, and simultaneously deny the legitimate requester their response. This matches the "cross-user response confusion" / unauthorized data disclosure impact class.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only unauthenticated access to submit signed messages to the legacy gateway `/user` HTTP endpoint (any valid ECDSA key works as `Sender`), and needs to choose/predict a `MessageId` that collides with a concurrently in-flight legitimate request. This is fully feasible when: (a) the target application uses low-entropy, sequential, or otherwise guessable `MessageId`s, (b) the attacker can induce/observe a target request being sent (e.g., via a webhook, shared frontend, or a compromised third-party integration point that shares knowledge of the ID), or (c) an attacker simply races many candidate IDs against a known window when a specific target ID will be used. No rate limiting currently blocks rapid repeated submissions with attacker-chosen IDs.

### Recommendation
Bind the saved callback to more than just `MessageId`: 
- Reject `HandleLegacyUserMessage` calls that reuse an in-flight `MessageId` (return an error, similar to the v2 `httpTriggerHandler.setupCallback` "already in-flight" check) instead of silently overwriting `savedCallbacks`.
- Additionally bind the callback entry to the original request's `Sender` (recovered signer address) and verify at delivery time (`handleWebAPITriggerMessage`) that the responding message's payload/context is consistent with the original sender/request, not just the `MessageId`.
- Apply the still-pending "allowlist and rate-limiting" TODO to reduce the ability of an attacker to race collisions.

### Proof of Concept
Go test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`, reusing `setupHandler`/`triggerRequest` helpers):
1. `handler, _, don, nodes := setupHandler(t)`; stub `don.On("SendToNode", ...).Return(nil)` for all calls.
2. Build `victimMsg := triggerRequest(t, victimKey, []string{"topic"}, "", "", "")` with `MessageId = "collide-id"` (override the helper's hardcoded ID or use a variant that accepts a custom ID).
3. `cbVictim := hc.NewCallback(); require.NoError(t, handler.HandleLegacyUserMessage(ctx, victimMsg, cbVictim))` — confirms `handler.savedCallbacks["collide-id"]` now holds `cbVictim`.
4. Build `attackerMsg := triggerRequest(t, attackerKey, []string{"topic"}, "", "", "")` with the same `MessageId = "collide-id"`.
5. `cbAttacker := hc.NewCallback(); require.NoError(t, handler.HandleLegacyUserMessage(ctx, attackerMsg, cbAttacker))` — assert `handler.savedCallbacks["collide-id"]` now points to `cbAttacker` (overwrite confirmed via direct map inspection under `handler.mu`).
6. Simulate the DON node responding to the *victim's* original request: `resp, _ := hc.ValidatedResponseFromMessage(victimMsg); err := handler.HandleNodeMessage(ctx, resp, nodes[0].Address); require.NoError(t, err)`.
7. Assert `cbAttacker.Wait(ctx)` returns successfully with a response whose payload matches `victimMsg`'s content (`codec.EncodeLegacyResponse(victimMsg)`), proving the attacker received the victim's response.
8. Assert `cbVictim.Wait(shortTimeoutCtx)` times out / never receives a response, proving the victim's callback was silently hijacked and orphaned.

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
