### Title
Unprivileged request-ID collision in the legacy Web API gateway handler causes cross-user response confusion / callback hijack - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Olympus TRSRY bug is a "measure-then-trust" race: the treasury records a balance snapshot, performs an external transfer, and later trusts the resulting delta without protecting the intervening window, letting an attacker slip unrelated funds into the window and get credited for someone else's action. The reachable analog in this repo is in the legacy Web API gateway handler (`core/services/gateway/handlers/capabilities/handler.go`), which records a pending-callback "snapshot" in `savedCallbacks` keyed only by the fully user-controlled JSON-RPC request `ID`, then later trusts whatever entry occupies that key when a DON node's response arrives — without any collision/duplicate protection, unlike the sibling implementation `RequestCache` (`core/services/gateway/handlers/common/requestcache.go`) which explicitly scopes the key by `{sender, id}` and rejects duplicates.

### Finding Description
The gateway's public, unauthenticated HTTP entry point `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) accepts a raw JSON-RPC request from any client and dispatches legacy requests to `handler.HandleLegacyUserMessage`. The request `ID` is fully attacker-controlled and is stamped directly onto the internal message: `m.Body.MessageId = req.ID` in `ValidatedMessageFromReq` (`core/services/gateway/handlers/common/message_util.go:36-58`).

In `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:411-414`):
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
The map is keyed **only** by `MessageId` — there is no per-sender/owner scoping and no rejection of an already in-flight ID (contrast with `requestCache.NewRequest`, `core/services/gateway/handlers/common/requestcache.go:50-76`, which uses `globalId{sender, id}` and explicitly errors with "request already exists" on collision).

When a DON node later responds with `MethodWebAPITrigger`, `handleWebAPITriggerMessage` (`handler.go:148-162`) looks the callback up purely by `MessageId`, deletes it, and delivers the response to whichever `Callback` currently occupies that key:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
...
return savedCb.SendResponse(...)
```

Because `map[key] = value` unconditionally overwrites, if two unrelated unprivileged clients submit requests using the same request `ID` (trivially guessable/collidable — request IDs are plain client-chosen strings per the gateway's own README: "User Requests: Plain string identifiers"), the second write clobbers the first client's stored `Callback`. This is exactly analogous to the TRSRY pattern: a benign "in-flight" window (between saving the callback and the node's async response arriving) can be polluted by an unrelated party's action landing in that same window, and the code trusts whatever it finds there when it finally reads back.

### Impact Explanation
An unprivileged client can cause the gateway to deliver another client's Web API Trigger response to the attacker's own HTTP connection instead of the legitimate requester's connection (cross-user response confusion), or conversely cause a legitimate user's response to silently vanish/be misdirected, effectively denying that user their result. Depending on payload contents (e.g., data returned from triggered workflow executions), this could leak information intended for a different caller to an unauthorized party purely through ID collision, with no authentication bypass needed — just knowledge/reuse of a request ID. This is a confidentiality/integrity issue in the internet-facing gateway's message routing, not a funds-movement bug, but it is a direct concrete instance of "cross-user response confusion" from an unprivileged client request.

### Likelihood Explanation
Likelihood is moderate: it requires two clients (or an attacker sending two overlapping requests) to use the identical request `ID` concurrently against the same DON while a request is in-flight (i.e., during the window between `HandleLegacyUserMessage` storing the callback and the corresponding node response arriving). Since IDs are plain attacker-chosen strings without per-sender namespacing, an attacker can trivially guess or brute-force short/likely IDs (e.g., "1", timestamps, or IDs discovered via traffic/log observation) and race a flood of requests with the same ID to increase the chance of colliding with a legitimate in-flight request before it completes.

### Recommendation
Scope the `savedCallbacks` map key by `{sender, MessageId}` rather than `MessageId` alone (mirroring `requestCache`'s `globalId{sender, id}` pattern), and reject/error on inserting a duplicate key for an already in-flight request instead of silently overwriting. Additionally, validate that responses are only delivered to the callback that logically owns them (e.g., verify sender/receiver signature binding matches the stored request's originator) before dispatching `SendResponse`.

### Proof of Concept
1. Client A sends a legitimate legacy JSON-RPC request to the gateway's user-facing HTTP endpoint with `id = "X"` and method `MethodWebAPITrigger`-routed payload; the gateway calls `HandleLegacyUserMessage`, which stores `h.savedCallbacks["X"] = callbackA` and forwards the request to DON members (`handler.go:411-419`).
2. Before the DON node responds, the attacker (Client B) sends a request with the same `id = "X"` to the gateway; `HandleLegacyUserMessage` runs again and overwrites `h.savedCallbacks["X"] = callbackB` (`handler.go:412`), since there is no collision check.
3. When the DON node eventually responds for Client A's original triggered execution with `MessageId = "X"`, `handleWebAPITriggerMessage` looks up `h.savedCallbacks["X"]`, finds `callbackB`, deletes the entry, and calls `callbackB.SendResponse(...)` (`handler.go:148-162`) — delivering Client A's response to Client B's HTTP connection instead of Client A's.

Note: I was unable to fully verify from the index whether an additional signature/receiver check exists further downstream in the DON→gateway response path that might partially mitigate delivery to the wrong owner (e.g., in `common.ValidatedMessageFromResp`); a background Devin session with full repo access would be needed to trace `ValidatedMessageFromResp` and confirm there is no sender-binding check that would prevent this exact PoC from succeeding end-to-end.