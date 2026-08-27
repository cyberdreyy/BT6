### Title
Unsigned integer arithmetic in Gateway `HandleLegacyUserMessage` staleness check allows attacker-controlled timestamp to bypass message-age validation - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The Gateway's legacy WebAPI trigger message handler validates message freshness using unsigned‑integer subtraction and an unchecked conversion of an attacker‑supplied signed timestamp field to `uint`, mirroring the root cause of the referenced `LPDA.getPrice()` bug class (unsigned subtraction/underflow-driven bypass of a safety check driven by attacker‑influenced inputs).

### Finding Description
`HandleLegacyUserMessage` decodes an unprivileged, gateway-facing request body into `webapicap.TriggerRequestPayload` and then performs the staleness check: [1](#0-0) [2](#0-1) 

The check is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // reject as stale
}
``` [3](#0-2) 

`payload.Timestamp` originates entirely from the untrusted, attacker-controlled JSON payload of an unauthenticated (at this stage) legacy user message and is only checked for the literal value `0` before use — there is no check for negative or absurdly large values. Because the comparison casts `payload.Timestamp` to `uint`, any attacker-supplied negative timestamp wraps around to a value close to `math.MaxUint64`/`MaxUint32` (platform-dependent `uint` width), which will always be larger than `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`. This causes the staleness check to always evaluate to `false`, i.e., the message is *never* rejected as stale, regardless of its actual age or of the "freshness" invariant the check is meant to enforce. This is structurally the same defect class as the LPDA bug: unsigned arithmetic combined with attacker-influenced values that were assumed always to satisfy typical/expected ranges, defeating a safety check instead of causing a hard revert.

Unlike the LPDA case (which reverts/bricks), here the wraparound causes a silent bypass of the intended validation rather than a revert, but the underlying flaw — signed/unsigned mismatch enabling attacker control over a safety comparison via out-of-range input — is the same bug class explicitly called out in the report.

### Impact Explanation
The `maxAllowedMessageAgeSec` staleness gate exists specifically to prevent old/replayed legacy trigger messages from being forwarded to DON nodes. Bypassing it via a crafted negative `Timestamp` allows an unprivileged client to have arbitrarily old or malformed timestamp-bearing trigger messages accepted and dispatched to all DON members via `don.SendToNode`, undermining the freshness guarantee the handler is documented to enforce: [4](#0-3) 

This weakens the anti-replay/staleness protection surrounding an internet-facing gateway message handler, one of the explicitly in-scope areas for this analysis (message envelopes/handlers). It does not on its own move funds, but it removes a validation layer relied upon to gate which requests are forwarded to DON nodes.

### Likelihood Explanation
The `Timestamp` field is part of the JSON body decoded directly from an unprivileged client's HTTP/gateway request with no range validation beyond `== 0`, so any external caller of the legacy web API trigger path can trivially supply a negative value to trigger the wraparound. No special privileges, node compromise, or race conditions are required.

### Recommendation
Change the freshness check to avoid unsigned wraparound: validate `payload.Timestamp` is within a sane, non-negative bound before use, and perform the comparison using signed arithmetic (`int64`) instead of casting to `uint`, e.g.:
```go
now := time.Now().Unix()
if payload.Timestamp <= 0 || payload.Timestamp > now || now-payload.Timestamp > int64(h.config.MaxAllowedMessageAgeSec) {
    // reject as stale/invalid
}
```
This both rejects negative/absurd timestamps explicitly and removes the unsigned-subtraction pattern responsible for the bypass.

### Proof of Concept
Not independently executed against a live environment (index access only), but the exploit path is directly derivable from the code:
1. An unprivileged client sends a legacy WebAPI trigger message with `Body.Payload.Timestamp = -1` (any negative int64).
2. `HandleLegacyUserMessage` passes the `payload.Timestamp == 0` check (it's `-1`, not `0`).
3. In the staleness check, `uint(payload.Timestamp)` evaluates to `18446744073709551615` (uint64) or the 32-bit wraparound equivalent, which is always greater than `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`.
4. The "stale message" branch is never entered, and the message proceeds to be recorded in `savedCallbacks` and forwarded to every DON member via `don.SendToNode`, regardless of the intended freshness window configured by `MaxAllowedMessageAgeSec`. [5](#0-4)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-346)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-384)
```go
	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
