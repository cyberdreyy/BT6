Confirmed: `DecodeJSONRequest` sets `msg.Body.MessageId = request.ID` directly from the raw, attacker-controlled JSON-RPC `id` field, with no uniqueness or per-sender binding enforced anywhere in the validation chain (`api/message.go` `Validate()` only checks length/null-suffix). This confirms the finding.

### Title
Callback hijack via unauthenticated MessageId collision in `handleWebAPITriggerMessage` - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`handler.HandleLegacyUserMessage` stores each in-flight web-API trigger callback in `h.savedCallbacks` keyed solely by the attacker-supplied `msg.Body.MessageId`, with no check that an entry for that ID doesn't already exist. Any unauthenticated client can send a signed trigger request using the same `MessageId` as a victim's in-flight request; the later request silently overwrites the earlier callback entry, so when the DON node's response for the victim's original request arrives, `handleWebAPITriggerMessage` delivers the victim's response to the attacker's callback instead.

### Finding Description
The public gateway endpoint accepts a signed `Message` whose `Body.MessageId` is taken verbatim from the JSON-RPC request `ID` supplied by the caller (`core/services/gateway/api/jsonrpccodec.go` `DecodeJSONRequest`, line 30: `msg.Body.MessageId = request.ID`). `Message.Validate()` (`core/services/gateway/api/message.go` lines 54-88) only checks length bounds and that the ID doesn't end in a null byte, and verifies the signature belongs to *some* valid signer — it never ties the `MessageId` to the specific caller or enforces global/per-handler uniqueness.

In `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go` lines 411-414):
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
```
there is no existence check before the map write — unlike sibling handlers `vault.handler.newActiveRequest` and `confidentialrelay.handler.newActiveRequest`, which both explicitly reject a request when `h.activeRequests[req.ID] != nil` ("request ID already exists"). This handler has no such guard, so a second request with a colliding `MessageId` silently replaces the first request's stored callback.

Exploit flow:
1. Victim submits a `web_api_trigger` request with `MessageId = "X"`; it's stored in `savedCallbacks["X"]` and forwarded to all DON members.
2. Before any DON node responds, attacker (any internet client with an arbitrary keypair, since the message just needs a valid ECDSA signature over the attacker's own message) submits a colliding `web_api_trigger` request also using `MessageId = "X"`. This overwrites `savedCallbacks["X"]` with the attacker's own `Callback`.
3. When a DON node responds to the *victim's* original request (the node echoes back the same `MessageId`), `handleWebAPITriggerMessage` (lines 148-162) looks up `savedCallbacks["X"]`, finds the attacker's callback (due to overwrite), deletes the entry, and calls `savedCb.SendResponse(...)` — delivering the victim's trigger response payload to the attacker's HTTP connection instead of the victim's.

No component in the path (signature validation, rate limiter, or `pruneCallbacks`) checks that the responding message actually corresponds to the same original sender/connection that populated the map entry; correlation is purely by the caller-controlled string key.

### Impact Explanation
This is a cross-user response confusion / request impersonation vulnerability: an unprivileged attacker can redirect another user's web-API trigger response to themselves. Depending on what data flows through the trigger response (which can include capability-produced payloads), this can expose data intended for another party. It maps to the "cross-user response confusion" / unauthorized disclosure impact class named in the target description, though for this specific `web_api_trigger` path the direct blast radius is the trigger response payload rather than vault secrets (vault and confidentialrelay handlers already have the missing-here duplicate-ID guard).

### Likelihood Explanation
Any internet client capable of producing a validly-signed gateway message (an arbitrary key pair; `Message.Validate()` only requires a well-formed ECDSA signature, not any privileged/allowlisted identity) can attempt this. The only precondition is timing the collision to land while the victim's request is in-flight (typically within the multi-second window before DON nodes reply), which is a standard, repeatable network race — no operator, admin, or node privilege required.

### Recommendation
Add an existence check before inserting into `h.savedCallbacks` in `HandleLegacyUserMessage`, mirroring `vault.handler.newActiveRequest`/`confidentialrelay.handler.newActiveRequest`: reject (return an error response) if `h.savedCallbacks[msg.Body.MessageId]` is already populated, instead of silently overwriting it. Additionally consider binding the stored callback to the original signer/sender address and verifying that binding when a node response is matched, so a colliding ID from a different sender cannot be substituted even if the first check were bypassed.

### Proof of Concept
Go handler-level test (add to `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Build two valid signed `triggerRequest` messages with different signing keys but the **same** `MessageId` ("dup-id").
2. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` — assert it's accepted and `savedCallbacks["dup-id"]` is set.
3. Before responding from a node, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` with the same `MessageId` — assert (bug) it succeeds and overwrites `savedCallbacks["dup-id"]` (currently no error), whereas after the fix, it should return an error like "request ID already exists" and `attackerCallback.Wait` should receive nothing / an explicit rejection.
4. Simulate a DON node responding to the victim's original message (`handler.HandleNodeMessage(ctx, respFromVictimRequest, nodeAddr)`).
5. Assert (bug, pre-fix): `attackerCallback.Wait(ctx)` receives the victim's response payload, while `victimCallback.Wait(ctx)` times out / never fires — demonstrating single-delivery-to-wrong-caller. Post-fix: `victimCallback.Wait(ctx)` receives the correct response and the attacker's colliding request was rejected up front.