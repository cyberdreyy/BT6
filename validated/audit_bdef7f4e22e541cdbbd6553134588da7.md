### Title
No replay protection for MessageId in `HandleLegacyUserMessage` allows duplicate re-execution of a captured signed request within the staleness window - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`handler.HandleLegacyUserMessage` only rejects messages that are older than `MaxAllowedMessageAgeSec`; it never records or checks whether a `MessageId` has already been consumed. Note the premise about forging a *fresh* `Timestamp` while reusing a stolen signature is incorrect, because the signature covers the raw `Payload` bytes (which include the `Timestamp` field) along with `MessageId`, `Method`, `DonId`, and `Receiver` — an attacker without the private key cannot alter the timestamp and keep the signature valid. However, a byte-for-byte replay of the exact captured envelope (same signature, same timestamp) is still possible and unmitigated as long as it is submitted before `MaxAllowedMessageAgeSec` elapses.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) performs these checks in order:
1. Unmarshal `payload` and confirm `Timestamp != 0`.
2. Reject if `now - MaxAllowedMessageAgeSec > payload.Timestamp` (staleness check only, lines 372-383).
3. Confirm `msg.Body.Method == MethodWebAPITrigger`.
4. Convert to a JSON-RPC request via `common.ValidatedRequestFromMessage`.
5. Store a `savedCallback` keyed by `msg.Body.MessageId` in `h.savedCallbacks` (line 412), **overwriting** any prior entry with the same key rather than rejecting it.
6. Forward the message to **every** DON member via `don.SendToNode` (lines 417-419).

At no point is there a check for whether this `MessageId` (or a hash of the signed envelope) has been seen before. `msg.Validate()` (core/services/gateway/api/message.go:54-88) only validates field lengths/format and recovers the signer address (`ExtractSigner`) — it does not perform any replay/nonce tracking either. The signature (`Sign`/`ExtractSigner`, message.go:90-134) is computed over `MessageId || Method || DonId || Receiver || Payload`, so a captured, validly-signed envelope can be resubmitted verbatim any number of times as long as `payload.Timestamp` (frozen inside the signed payload) is still within `MaxAllowedMessageAgeSec` of `time.Now()`. Each resubmission re-enters step 5/6 and is forwarded to all DON nodes again, producing duplicate downstream processing (e.g., duplicate `web_api_trigger` invocations) for what looks like a single legitimate user action.

### Impact Explanation
This matches the "duplicate/duplicate-cost execution of a victim's authorized action" impact class: an attacker who intercepts one valid signed envelope (e.g., observed on a non-confidential channel) can resubmit it multiple times within the message-age window, causing the gateway to forward the same trigger/action to the entire DON repeatedly. Depending on downstream node-side idempotency (not implemented in this handler and not verified to exist elsewhere in the traced path), this can cause repeated execution of the victim's request, e.g., duplicated compute/HTTP calls or duplicated workflow triggers, consuming resources under the victim's identity without their consent.

### Likelihood Explanation
Feasible and fully repeatable within `MaxAllowedMessageAgeSec` (config-defined, no enforced upper bound observed in `HandlerConfig`): the attacker only needs to capture one prior valid signed message body+signature (no credential theft, no signing key needed) and can then simply repost the identical bytes to the gateway's message endpoint any number of times before the timestamp ages out. No additional privilege is required beyond passively observing one gateway message.

### Recommendation
Add a replay-protection cache in `handler` (similar in spirit to the existing `savedCallbacks` map, or dedicated) that records consumed `MessageId`s (or a hash of the full signed envelope) with TTL ≥ `MaxAllowedMessageAgeSec`, and reject `HandleLegacyUserMessage` calls whose `MessageId` has already been seen instead of silently overwriting the `savedCallbacks` entry at line 412. This should be checked before forwarding the request to DON members.

### Proof of Concept
Go test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a valid signed `api.Message` with `MethodWebAPITrigger`, a fixed `MessageId`, and `payload.Timestamp = time.Now().Unix()`.
2. Call `handler.HandleLegacyUserMessage(ctx, msg, callback1)` — assert it succeeds and `don.SendToNode` is invoked once per DON member (verify via mock `handlers.DON`).
3. Immediately call `handler.HandleLegacyUserMessage(ctx, msg, callback2)` again with the **same** message bytes/signature (simulating replay) while still within `MaxAllowedMessageAgeSec`.
4. Assert (post-fix) that the second call is rejected with a "duplicate message" / "replay detected" error and `don.SendToNode` is **not** invoked a second time.
5. Currently (pre-fix), assert that the second call succeeds and `don.SendToNode` is invoked again, and that `h.savedCallbacks[MessageId]` was overwritten — demonstrating the missing replay check.