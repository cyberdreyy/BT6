This is confirmed by the code. `msg.Body.MessageId` is user-supplied via the JSON-RPC request ID (`req.ID`) at `core/services/gateway/handlers/common/message_util.go:52`, and `HandleLegacyUserMessage` stores the callback keyed only by that ID with no existence/collision check before overwrite.

### Title
Cross-user response hijacking via unchecked MessageId collision in savedCallbacks map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each user's callback in `h.savedCallbacks` keyed solely by the client-controlled `msg.Body.MessageId`, with no check that the key is already in use and no binding to the requester's identity. A second request racing with the same `MessageId` silently overwrites the first entry, so the node's response to the first (victim) request is delivered to whichever callback is currently registered — potentially the attacker's.

### Finding Description
`ProcessRequest` in `core/services/gateway/gateway.go:218` decodes the JSON-RPC request and passes `jsonRequest.ID` through to become `msg.Body.MessageId` in `ValidatedMessageFromReq` (`core/services/gateway/handlers/common/message_util.go:36-58`). This ID is fully attacker-chosen (bounded only to ≤200 chars in `gateway.go:228` and ≤128 chars / no trailing null in `Message.Validate` at `core/services/gateway/api/message.go:54-88`) — there is no server-side uniqueness enforcement or per-sender namespacing.

In `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:411-414`):
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
there is no check like `if _, exists := h.savedCallbacks[id]; exists { reject }` before overwriting. If an attacker submits their own signed request using the same `MessageId` as a victim's in-flight request (attacker fully controls their own request's ID field, requiring only a valid signature over their own message — no privileged access), the attacker's callback silently replaces the victim's in `savedCallbacks`.

Later, `handleWebAPITriggerMessage` (`handler.go:148-162`), invoked from `HandleNodeMessage` when a DON node responds, looks up and deletes `h.savedCallbacks[msg.Body.MessageId]` purely by that ID — with no check that the response corresponds to the sender who originally registered it:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
if found {
    return savedCb.SendResponse(...)
}
```
Whichever node response for that `MessageId` arrives first is delivered to whatever callback currently occupies the map slot. Since the DON echoes back the `MessageId` from whichever original request it processed (either the victim's or attacker's), and both requests share an ID, the first node response received for that ID gets routed to the currently-registered callback — which may belong to the other party, not the request that produced that response.

### Impact Explanation
This causes cross-user response confusion: an attacker can potentially receive the victim's web API trigger response, or cause the victim's HTTP client to hang and instead deliver an attacker-controlled/attacker-triggered node response into the victim's outstanding HTTP callback (impacting whichever party's connection is currently bound in the map when the reply lands). This matches the "cross-user response confusion / hijacked capability result delivery" impact class — an unprivileged HTTP client can cause information intended for another user's request to be misdirected.

### Likelihood Explanation
Exploitability requires only: (1) ability to send a signed gateway request as any client (no operator/admin credentials, no compromised node/DON needed) — self-signing is standard client behavior; (2) knowledge or guessing/observation of a victim's chosen `MessageId`/JSON-RPC request `ID` value and timing the attacker's own request within the victim's callback window (`CallbackMaxAgeSec`, default 120s, `handler.go:43`). Many client implementations use predictable or low-entropy IDs (sequential counters, timestamps, short UUID prefixes), making collisions and races feasible, though it does require some way to predict/observe the victim's chosen ID, which is an external, application-level precondition. The bug itself (no uniqueness check, no sender binding) is deterministic and 100% reproducible once the ID collision and race condition are met.

### Recommendation
Bind `savedCallbacks` entries to the requester's identity in addition to `MessageId` (e.g., derive the key from a hash of `(Sender, MessageId)` or use a server-generated, unpredictable internal ID unrelated to the client-supplied one), and reject/refuse registration in `HandleLegacyUserMessage` if an unexpired entry already exists for the same key rather than silently overwriting it. Additionally, `handleWebAPITriggerMessage` should verify the node's response is being routed to the callback that actually issued the fanned-out request (e.g., validate against a stored sender/session identifier) rather than trusting the bare `MessageId` match.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler with the DON config used in existing tests (see `nodes` setup in that file).
2. Create callback `cbVictim := hc.NewCallback()`; call `handler.HandleLegacyUserMessage(ctx, victimMsg, cbVictim)` where `victimMsg.Body.MessageId = "shared-id"` signed by `nodes[0].PrivateKey`/victim key — assert `handler.savedCallbacks["shared-id"]` is `cbVictim`'s entry.
3. Create callback `cbAttacker := hc.NewCallback()`; call `handler.HandleLegacyUserMessage(ctx, attackerMsg, cbAttacker)` with `attackerMsg.Body.MessageId = "shared-id"` (same ID, different signer/sender) — assert `handler.savedCallbacks["shared-id"]` now points to `cbAttacker` (overwrite confirmed).
4. Construct a node response `respMsg` with `Body.MessageId = "shared-id"` (as if replying to the victim's original request) signed by the node, and call `handler.HandleNodeMessage(ctx, resp, nodeAddr)`.
5. Assert `cbAttacker.Wait(ctx)` receives the response (`require.NoError` and matching payload), and `cbVictim.Wait(ctx)` times out / never resolves — demonstrating the victim's expected response was delivered to the attacker's callback instead, violating per-sender isolation.