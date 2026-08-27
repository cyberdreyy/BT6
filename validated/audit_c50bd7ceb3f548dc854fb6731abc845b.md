### Title
Rate-limiter bucket-key mismatch allows per-sender quota bypass via case/format aliasing of `body.Sender` - ([File: core/capabilities/webapi/trigger/trigger.go])

### Summary
`triggerConnectorHandler.processTrigger` authorizes senders by normalizing the address with `ethCommon.HexToAddress(...).String()`, but then rate-limits using the raw, attacker-supplied `body.Sender` string directly. Because the two checks key on different representations of the same address, an attacker who controls the literal `Sender` string in their signed gateway message can defeat per-sender rate limiting while still passing the allowlist check.

### Finding Description
In `processTrigger` [1](#0-0) , the allowlist check uses the normalized address:
```go
if !trigger.allowedSenders[sender.String()] { ... }
if !trigger.rateLimiter.Allow(body.Sender) { ... }
```
`sender` is derived from `ethCommon.HexToAddress(body.Sender)` in `HandleGatewayMessage` [2](#0-1) , which parses any hex-case variant (`0xabc...`, `0xABC...`, mixed) into the same 20-byte `common.Address` value and re-serializes it via `.String()` to a single canonical (EIP-55 checksummed) representation. This makes the allowlist check case-insensitive/canonical, as intended.

However, `trigger.rateLimiter.Allow(body.Sender)` is called with the raw, unnormalized string taken directly from the attacker-controlled message body — not `sender.String()`. If the rate limiter buckets requests by the literal string key it receives (standard behavior for a per-sender token-bucket keyed by a string map), then two requests whose `body.Sender` values differ only in hex case (e.g., `0x853d51d5d9935964267a5050ac53aa63eca39bc5` vs `0x853D51D5D9935964267A5050AC53AA63ECA39BC5`) will:
1. Both resolve to the same `common.Address` and pass the `allowedSenders` check identically.
2. Be treated as two distinct keys by `ratelimit.RateLimiter.Allow`, each getting its own independent bucket/quota.

Since the gateway signature is computed over the raw serialized message body bytes (including the literal `Sender` field text) via `gw_common.Flatten(api.GetRawMessageBody(body)...)` [3](#0-2) , and the attacker is the legitimate holder of the signing key for their own address, they can freely choose the literal casing of `body.Sender` for each message and produce a fresh, validly-signed message each time — as long as message validation (`msg.Validate()` in `ValidatedMessageFromReq`) checks sender authenticity via address-equality (parsing to `common.Address`) rather than exact-string equality, which is the pattern used throughout this file. This effectively lets the attacker mint an unbounded number of independent rate-limit buckets for what the authorization layer treats as one identity.

### Impact Explanation
This is a per-sender rate-limit / allowlist-quota bypass (`PerSenderRPS`/`PerSenderBurst`) against the shared workflow trigger channel (`trigger.ch`, buffered at `defaultSendChannelBufferSize = 1000` [4](#0-3) [5](#0-4) ). An allowlisted sender can multiply their effective quota by simply varying the hex case of their address string per request, exceeding the configured per-sender RPS/burst and flooding the trigger channel / downstream workflow execution pipeline, causing resource exhaustion (denial-of-service against the shared trigger channel and, by extension, workflow execution).

### Likelihood Explanation
Precondition: attacker must be an allowlisted sender (`AllowedSenders`) — i.e., minimal capability already granted to send trigger requests, matching the described unprivileged "signed gateway request sender" actor. No other privilege is required. Constructing case-variant addresses is trivial (any hex string case-folds to the same `common.Address`), and each variant is independently signable by the same private key, so the bypass is fully repeatable and requires no race conditions or timing tricks.

### Recommendation
Normalize the key used for rate limiting to match the authorization key. Call `trigger.rateLimiter.Allow(sender.String())` instead of `trigger.rateLimiter.Allow(body.Sender)` in `processTrigger`, so both the allowlist and the rate limiter operate on the canonicalized `common.Address` string.

### Proof of Concept
Go unit test plan (extending `core/capabilities/webapi/trigger/trigger_test.go`):
1. Register a trigger with `AllowedSenders: []string{address1}` and a tight `PerSenderRPS`/`PerSenderBurst` (e.g., RPS=1, Burst=1).
2. Build two signed gateway requests using the same private key (`privateKey1`), but manually set `Body.Sender` to two different hex-case variants of the same address (e.g., lowercase and EIP-55 checksummed) before signing, using `gatewayRequest`-style helper modified to accept an explicit `Sender` override, then re-sign so the signature matches the modified body.
3. Send the first request via `HandleGatewayMessage` — expect `ACCEPTED` and a trigger event on the channel.
4. Immediately send the second (case-variant) request — expect it to be rejected as rate-limited (`Status: "ERROR"`, `"request rate-limited for sender ..."`) if the fix is applied.
5. Without the fix, assert (documenting the bug) that the second request is also `ACCEPTED` and delivered on the channel, demonstrating that the burst-of-1 quota was exceeded by using a different-case `Sender` string — i.e., the rate limiter treated the two identical addresses as separate buckets.

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L29-29)
```go
const defaultSendChannelBufferSize = 1000
```

**File:** core/capabilities/webapi/trigger/trigger.go (L97-109)
```go
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L157-158)
```go
	body := &msg.Body
	sender := ethCommon.HexToAddress(body.Sender)
```

**File:** core/capabilities/webapi/trigger/trigger.go (L241-241)
```go
	ch := make(chan capabilities.TriggerResponse, defaultSendChannelBufferSize)
```

**File:** core/capabilities/webapi/trigger/trigger.go (L322-326)
```go

	signature, err := h.connector.SignMessage(ctx, gw_common.Flatten(api.GetRawMessageBody(body)...))
	if err != nil {
		return err
	}
```
