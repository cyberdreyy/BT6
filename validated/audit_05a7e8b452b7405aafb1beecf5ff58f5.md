This confirms the finding: other handlers in this codebase (e.g. `RequestCache` in `core/services/gateway/handlers/common/requestcache.go`) key pending requests by `globalId{sender, id}` — combining the message sender with the message ID — precisely to prevent this exact cross-user collision. The `capabilities` handler's `savedCallbacks` map, however, is keyed by `msg.Body.MessageId` alone, with no `Sender` binding, and `MessageId` is fully attacker-controlled and unauthenticated (any signer can pick any string, per `api.Message.Validate` in `core/services/gateway/api/message.go`).

### Title
Cross-user response hijacking via `MessageId` collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores a user's callback in `h.savedCallbacks` keyed solely by the attacker-controlled `msg.Body.MessageId`, without binding it to the requester's identity (`msg.Body.Sender`). Any unauthenticated gateway client can choose an arbitrary `MessageId`, so a second user submitting the same `MessageId` as an in-flight victim request overwrites the victim's saved callback, causing the eventual node response for that `MessageId` to be delivered to the attacker instead of the victim.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go`) validates only the message signature/format via `msg.Validate()` but does not enforce any uniqueness or ownership binding of `MessageId` per sender; it then calls `h.HandleLegacyUserMessage(ctx, msg, callback)`.

In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` does: [1](#0-0) 
This unconditionally overwrites any existing entry at `msg.Body.MessageId` in the map, with no check for an existing entry nor comparison against `msg.Body.Sender`.

Later, `handleWebAPITriggerMessage` looks up and pops the callback purely by ID: [2](#0-1) 
and delivers whichever response the node sent for that `MessageId` to whichever callback currently occupies that slot in `savedCallbacks`.

Because `api.Message.Body.MessageId` is attacker-supplied and unauthenticated content (only signature and format are validated in `Validate()`, see `core/services/gateway/api/message.go` lines 54-88), any client can choose the same `MessageId` as a victim's in-flight request. If attacker B submits a request with `MessageId = "X"` after victim A's request with the same ID is already in-flight (in `savedCallbacks["X"]`), B's call to `HandleLegacyUserMessage` overwrites A's saved callback. When the node's response for ID `"X"` (which was fanned out for A's original request) arrives, `handleWebAPITriggerMessage` delivers it to B's callback (i.e., B's HTTP response), not A's.

This is a genuine design gap: the codebase demonstrates elsewhere (`core/services/gateway/handlers/common/requestcache.go`, `globalId{sender, id}` key) that the correct mitigation is to scope the response cache key by `(sender, MessageId)`, not `MessageId` alone. The capabilities handler's `savedCallbacks` fails to apply this pattern.

### Impact Explanation
This is a cross-user response/data confusion vulnerability: a capability result or trigger response intended for user A's request can be delivered to attacker B's connection. Depending on payload content (e.g., an HTTP body from `web_api_trigger`/`web_api_target` responses relayed via `sendHTTPMessageToClient`), this can leak data belonging to the victim's request to an unrelated, unauthenticated attacker, violating the isolation invariant between concurrent gateway users.

### Likelihood Explanation
Exploitability requires only that the attacker be an unauthenticated (or minimally credentialed) gateway client capable of signing and submitting a `web_api_trigger` message with an arbitrary `MessageId` — no elevated privilege is needed since gateway user messages only require a valid ECDSA signature, not membership in any particular allowlist for this check (`// TODO: apply allowlist and rate-limiting here` at line 384 confirms no allowlist is enforced yet). The main precondition is winning a race: submitting request B with the same `MessageId` after the victim's request A is saved but before the node responds — a narrow but realistic window given the asynchronous node fan-out (`don.SendToNode` for each DON member) and 120-second default callback TTL (`defaultCallbackMaxAgeSec`), making repeated attempts feasible.

### Recommendation
Key `savedCallbacks` by a composite of `(msg.Body.Sender, msg.Body.MessageId)` instead of `MessageId` alone (mirroring `globalId` in `core/services/gateway/handlers/common/requestcache.go`), and reject/no-op `HandleLegacyUserMessage` if an entry for that composite key already exists rather than silently overwriting it. Correspondingly, `handleWebAPITriggerMessage` must derive the same composite key from the node response (using `msg.Body.Receiver`/original sender) to look up the correct callback.

### Proof of Concept
1. Construct `handler` via `NewHandler` with a DON of at least one member (as in `setupHandler` in `handler_test.go`).
2. Build two signed `api.Message`s, `msgA` (victim key) and `msgB` (attacker key), both with `Body.MessageId = "X"` and valid `TriggerRequestPayload`, using the `triggerRequest` helper from `core/services/gateway/handlers/capabilities/handler_test.go`.
3. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)`, then `handler.HandleLegacyUserMessage(ctx, msgB, cbB)`.
4. Assert (currently failing) that `handler.savedCallbacks["X"]` still corresponds to `cbA`, or that the second call returns an error/rejects the duplicate ID.
5. Simulate node response for `MessageId = "X"` via `handler.HandleNodeMessage(ctx, resp, nodeAddr)` and assert only `cbA.Wait(ctx)` receives the response while `cbB.Wait(ctx)` times out — the current implementation will instead deliver the response to `cbB`.

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
