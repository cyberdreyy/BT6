### Title
Unsigned integer underflow in stale-message check allows bypass of message freshness validation - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.HandleLegacyUserMessage` function in the gateway's web-api-capabilities handler validates that an incoming `web_api_trigger` message is not stale by comparing a threshold to the attacker-supplied `payload.Timestamp` field. The comparison is performed entirely in the `uint` (unsigned) domain, and the signed `payload.Timestamp` value is cast directly to `uint` without a range/sign check, mirroring the exact bug class in the referenced report where a caller-controlled duration/timestamp value is truncated/cast into a smaller/different integer type without bounds checking, corrupting the computed comparison.

### Finding Description
The staleness check is: [1](#0-0) 

```go
if payload.Timestamp == 0 { ... }
...
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // "stale message"
}
```

`payload.Timestamp` originates from `webapicap.TriggerRequestPayload`, which is decoded straight from the unprivileged user's JSON request body via `json.Unmarshal(body.Payload, &payload)`: [2](#0-1) 

The only validation performed is that `Timestamp != 0`; there is no check that the timestamp is non-negative or within a sane bound before it is cast with `uint(payload.Timestamp)`. Because `payload.Timestamp` is a signed integer field fully controlled by the requesting client (any unprivileged web API caller sending a `web_api_trigger` message through the gateway), an attacker can supply a negative value. Casting a negative signed integer to `uint` in Go wraps around to a very large unsigned value (analogous to the Solidity `uint256`→`uint64` truncation/overflow described in the report, where casting an out-of-range value into a narrower/different unsigned type silently produces an unexpected huge or wrapped result instead of erroring). This causes the right-hand side of the comparison, `uint(payload.Timestamp)`, to become astronomically large, so the staleness check `lhs > rhs` will always evaluate `false` — the "stale message" branch is never taken regardless of how old (or replayed) the message actually is.

### Impact Explanation
This lets an unprivileged client permanently bypass the intended message-freshness/anti-replay control (`MaxAllowedMessageAgeSec`) for `web_api_trigger` requests processed by `handleLegacyUserMessage`, which forwards the request to all DON member nodes for execution: [3](#0-2) 
An attacker could resubmit or fabricate messages with negative/crafted timestamps to have them treated as always-fresh, undermining the staleness/anti-replay guarantee the gateway relies on before dispatching triggers to the workflow DON. Because timing/staleness checks are a security control layered in front of otherwise-unauthenticated legacy trigger dispatch (the code comment even notes "TODO: apply allowlist and rate-limiting here"), defeating it weakens one of the few gating mechanisms present on this path.

### Likelihood Explanation
High likelihood of reachability: `payload.Timestamp` is a plain field on the JSON payload of an unprivileged, internet-facing gateway endpoint (`HandleLegacyUserMessage`), requiring no authentication beyond whatever minimal checks exist for `web_api_trigger`. Supplying a negative timestamp is trivial for any external caller.

### Recommendation
Validate `payload.Timestamp` is non-negative (and within a sane bound) before using it, and perform the staleness comparison entirely in a signed 64-bit domain to avoid unsigned wraparound, e.g.:
```go
now := time.Now().Unix()
if payload.Timestamp < 0 || payload.Timestamp > now {
    // reject invalid timestamp
}
if now-int64(h.config.MaxAllowedMessageAgeSec) > payload.Timestamp {
    // stale message
}
```

### Proof of Concept
1. An unprivileged client sends a `web_api_trigger` legacy message to the gateway with `payload.Timestamp = -1` (or any negative value).
2. In `HandleLegacyUserMessage`, `payload.Timestamp != 0`, so it passes the zero-check. [4](#0-3) 
3. The staleness check computes `uint(payload.Timestamp)`, which wraps to a value near `math.MaxUint`/`math.MaxUint64` (huge), so `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` is always `false`.
4. The message is treated as fresh and forwarded to all DON members via `don.SendToNode`, bypassing the intended freshness/anti-replay gate. [5](#0-4) 

Note: I was unable to directly view the exact field declaration line for `Timestamp` inside `core/capabilities/webapi/webapicap/event_trigger_generated.go` (the index returned only match counts, not the surrounding declaration), so the precise Go type (`int64` vs `int`) of `TriggerRequestPayload.Timestamp` is inferred from the explicit `uint(payload.Timestamp)` cast in the handler, which would be unnecessary if the field were already unsigned. A Devin session with full repository access could confirm the exact field type if further certainty is needed.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-357)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
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
