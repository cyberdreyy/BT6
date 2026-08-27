### Title
Cross-user response confusion via attacker-controlled MessageId collision in savedCallbacks map - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` stores each request's `handlers.Callback` in `h.savedCallbacks` keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to the requester's identity (`Sender`). A second, unrelated caller who submits a request with the same `MessageId` while the first is still in flight overwrites the map entry, so the first caller's callback is silently orphaned while the DON's eventual response (delivered by `handleWebAPITriggerMessage`, which looks up and deletes by the same key) is routed to whichever callback currently occupies the slot.

### Finding Description
`MessageId` originates entirely from the caller: the gateway's `ProcessRequest` (`core/services/gateway/gateway.go:218-292`) takes `jsonRequest.ID` from the raw JSON-RPC request and threads it into `msg.Body.MessageId`, then invokes `h.HandleLegacyUserMessage(ctx, msg, callback)` synchronously per HTTP request. `Message.Validate()` (`core/services/gateway/api/message.go:54-88`) only checks length/format of `MessageId` and verifies the ECDSA signature to populate `Body.Sender`, but never checks `MessageId` for uniqueness across concurrent requests, nor binds it to `Sender` in the `savedCallbacks` map key.

In `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:411-414`):
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
the map key is only `msg.Body.MessageId`. Any two signed, valid requests (from any two distinct signers) using the same `MessageId` string will cause the second call to silently replace the first entry's `*savedCallback` (and therefore its `Callback`) in the map.

When a DON node later responds, `handleWebAPITriggerMessage` (`core/services/gateway/handlers/capabilities/handler.go:148-162`) does:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
```
It looks up and deletes by `MessageId` alone — it has no way to tell which of the two competing requests the response belongs to. Whichever request's callback is currently stored (the most recent writer) receives the response, and the callback that was overwritten is orphaned; the corresponding `gateway.ProcessRequest` call (`core/services/gateway/gateway.go:278`) will eventually time out on `callback.Wait(ctx)` and return a generic timeout error, having received nothing. If the attacker's overwriting request is processed while the victim's original DON responses are still arriving, the attacker's callback may receive a response payload that was generated in reply to the victim's original signed request/topic, achieving cross-user response confusion.

### Impact Explanation
This is a request/response confusion issue enabling one unprivileged caller to intercept another concurrent caller's DON response by MessageId collision, and to deny the original caller a response (denial of service for that specific request). It matches the "cross-user response confusion / attacker-controlled data returned to another user" impact class. It does not grant fund movement, key disclosure, or authentication bypass, and its severity is bounded by whatever data the DON response payload contains (e.g., webhook/trigger acknowledgement content), which in the current legacy `web-api-trigger` flow is limited but nonetheless is data belonging to another caller's request context.

### Likelihood Explanation
Exploitability requires only that the attacker be able to submit signed gateway requests (any low-privileged holder of a valid signing key for the DON — this is the standard capability of any legitimate gateway user), and that the attacker send a request with a `MessageId` matching a value that is concurrently in flight from another caller. The main precondition is knowledge or prediction of the victim's `MessageId` at the right time — since `MessageId` is fully client-chosen and the CLI/reference client (`core/scripts/gateway/web_api_trigger/invoke_trigger.go:56,100`) even defaults to a static `"12345"`, IDs are not guaranteed random/unique, making collisions plausible in practice for scripted or default-configured clients. No allowlist, rate limiter, or existing check in `HandleLegacyUserMessage` rejects a `MessageId` already present in `savedCallbacks`, so the race is fully repeatable via two concurrent `HandleLegacyUserMessage` calls sharing the same id.

### Recommendation
Key `savedCallbacks` by a composite of `Sender` and `MessageId` (or a server-generated, unpredictable internal correlation ID separate from the client-supplied `MessageId`), and reject/queue a new request whose `(Sender, MessageId)` (or raw `MessageId`, at minimum) already has an in-flight entry rather than silently overwriting it. Additionally, validate that a returning node response's `Sender`/DON member matches, and bind delivery back only to the callback that issued the exact matching request.

### Proof of Concept
Go handler-level test plan (extends `core/services/gateway/handlers/capabilities/handler_test.go`):
1. `setupHandler(t)` to obtain `handler`, `don` mock, and `nodes`.
2. Build `msgA := triggerRequest(t, nodes[0].PrivateKey, ..., messageID="dup-id")` signed by key A (simulating user A), and `msgB` with the **same** `MessageId="dup-id"` but signed by a different key (simulating attacker/user B), both passing validation.
3. Call `cbA := hc.NewCallback(); err := handler.HandleLegacyUserMessage(ctx, msgA, cbA)`; assert `err == nil` and `handler.savedCallbacks["dup-id"].Callback == cbA` (via a lock+read helper).
4. Call `cbB := hc.NewCallback(); err = handler.HandleLegacyUserMessage(ctx, msgB, cbB)`; assert `handler.savedCallbacks["dup-id"].Callback` is now `cbB`, i.e., `cbA` was overwritten (`require.NotEqual`/`require.Same` checks against the previous reference).
5. Simulate a DON node response for `"dup-id"`: `resp, _ := hc.ValidatedResponseFromMessage(msgB)`; `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)` (or `handleWebAPITriggerMessage`).
6. Assert `cbB.Wait(ctx)` returns the response successfully, while `cbA.Wait(ctx)` with a short-lived context times out / never resolves — proving user A's original request never received a response and user B's callback consumed the slot intended for the collision, demonstrating overwrite instead of rejection.