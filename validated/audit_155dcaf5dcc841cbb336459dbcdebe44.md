### Title
Cross-User Response/Callback Hijack via `MessageId` Collision in `savedCallbacks` Map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`ValidatedMessageFromReq` copies the outer JSON-RPC envelope's `req.ID` directly into `m.Body.MessageId` with only length/format checks, and the gateway's `savedCallbacks` map in `core/services/gateway/handlers/capabilities/handler.go` is keyed solely by this attacker-chosen `MessageId` — with no binding to the sender/requester. Any authenticated-but-unprivileged client who knows (or predicts) a victim's in-flight `MessageId` can submit their own signed request with the same ID, silently overwriting the victim's saved callback, so that the DON's eventual response is delivered to the attacker instead of the victim.

### Finding Description
`ValidatedMessageFromReq` (`core/services/gateway/handlers/common/message_util.go:36-58`) sets `m.Body.MessageId = req.ID` and only enforces length/null-byte rules via `Message.Validate` (`core/services/gateway/api/message.go:54-88`); it never checks that the ID is unique or bound to the calling sender beyond the fact that the sender's own signature happens to cover the `MessageId` bytes (`GetRawMessageBody`, `message.go:136-146`). Critically, a signature only proves who signed a message — it does not prevent an attacker from *choosing* to sign the same `MessageId` string that a victim is already using; nothing in `Validate()` checks for ID collisions against existing state.

The vulnerable sink is `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`), which unconditionally does:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
```
with no check for an existing entry under that key. If a victim's request with `MessageId = X` is still pending (DON nodes haven't responded yet), and an attacker submits a second, independently-signed request also using `MessageId = X`, the attacker's callback silently replaces the victim's in the map.

When a DON node later responds for ID `X`, `HandleNodeMessage` → `handleWebAPITriggerMessage` (`handler.go:148-162`) does:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
```
This looks up strictly by `MessageId`, with no correlation to which sender originated the specific pending request. Whichever request last inserted a callback for `X` receives the response — meaning the victim's response can be delivered to the attacker's connection, and the victim receives no response (silent drop) or a timeout.

The `dummyHandler` implementation (`core/services/gateway/handlers/handler.dummy.go:62-82`, `84-108`) has the identical pattern.

### Impact Explanation
This is a cross-user response confusion / callback hijack: an attacker can intercept another user's in-flight gateway response (e.g., a WebAPI trigger/target result meant for the victim's workflow), and the victim's original request effectively fails or is dropped. Depending on the payload content, this could expose data intended only for the victim's session/callback channel, matching Chainlink's "request impersonation / cross-user response confusion" impact class.

### Likelihood Explanation
Exploitability depends on the attacker being able to guess or observe a victim's `MessageId` before the DON responds. The finding text's own precondition acknowledges this ("attacker aware of a victim's pending request ID, e.g., predictable IDs"). If IDs are client-generated and not cryptographically random/high entropy (nothing in `Validate()` enforces randomness or entropy — only length ≤128 and no trailing null byte), or if IDs are observable/predictable (e.g., sequential, timestamp-based, or leaked via logs/other channels), this is straightforward and repeatable: the attacker only needs to be able to sign and submit their own legacy/JSON-RPC gateway request, which requires no special privilege beyond normal signed-client access.

### Recommendation
Do not key `savedCallbacks` purely by client-supplied `MessageId`. Either:
1. Generate the correlation key server-side (e.g., gateway-assigned UUID) and never trust client-supplied `req.ID` for callback indexing, or
2. Key the map by a composite of `(Sender, MessageId)` so that only the original signer of a given `MessageId` can look up/overwrite the entry, and
3. In `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage`, reject the request (return an error to the caller) if an entry already exists in `savedCallbacks` for the given key instead of silently overwriting it.

### Proof of Concept
Go handler-level integration test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Set up `handler` with a mocked `don` that captures `SendToNode` calls but doesn't respond immediately.
2. Victim: call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` where `victimMsg.Body.MessageId = "shared-id"`, signed with victim's key. Assert `handler.savedCallbacks["shared-id"].Callback == victimCallback`.
3. Attacker: call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` where `attackerMsg.Body.MessageId = "shared-id"` too, but signed with a different (attacker) key. Assert `handler.savedCallbacks["shared-id"].Callback` is now `attackerCallback`, i.e., it overwrote the victim's entry.
4. Simulate a node response for `MessageId = "shared-id"` via `handler.HandleNodeMessage(ctx, nodeResp, nodeAddr)` and assert that `attackerCallback.Wait(ctx)` receives the response payload while `victimCallback.Wait(ctx)` times out / never receives a response — demonstrating the cross-user hijack and confirming violation of the per-request isolation invariant.