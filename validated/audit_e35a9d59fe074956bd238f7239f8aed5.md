### Title
Gateway `web_api_trigger` message freshness check only enforces a lower bound, allowing arbitrarily future-dated timestamps to permanently bypass staleness/replay protection - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The reported bug class is a one-sided range validation on a client-supplied time value (`start_slot <= clock.slot`) that omits the opposite bound, letting a caller pick a value that skips the intended progression logic. The same pattern exists in the Chainlink gateway's `HandleLegacyUserMessage` handler, which is the internet-facing entry point for unprivileged user messages routed to a workflow DON.

### Finding Description
`HandleLegacyUserMessage` validates the user-supplied `payload.Timestamp` field with a single, one-directional comparison: [1](#0-0) 

The check `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` only rejects messages whose timestamp is *too old*. There is no corresponding upper-bound check that the timestamp is not unreasonably far in the future. `payload.Timestamp` comes directly from the untrusted, client-constructed `TriggerRequestPayload` body (unmarshalled at line 345 before any bound validation), which is signed and sent by an external caller through the gateway's public message-handling path — the same class of internet-facing envelope entry point the scope calls out (message envelopes/handlers).

Because the staleness comparison is one-sided, a caller can construct (and sign) a request with `payload.Timestamp` set arbitrarily far in the future. Such a message will never satisfy the "stale message" condition, no matter how much wall-clock time passes, since `now - MaxAllowedMessageAgeSec` will (for any realistic horizon) remain smaller than the forged future timestamp. This mirrors the reported root cause exactly: a schedule/freshness guard implemented as `value <= now` (or its inverse, `now - window > value`) without an accompanying `value >= now - tolerance` / `value <= now + tolerance` bound, letting the untrusted input escape the intended monotonic window.

### Impact Explanation
The `MaxAllowedMessageAgeSec` staleness gate exists specifically to bound how long a signed user message remains acceptable to the gateway before being forwarded to DON nodes (`don.SendToNode`, line 417-419). By forging a future timestamp, an unprivileged client can defeat this freshness bound for messages it legitimately signed, effectively disabling the intended lifetime enforcement for its own requests and allowing the same signed envelope to be resubmitted/reprocessed by the gateway well beyond the configured `MaxAllowedMessageAgeSec` window. This is a Medium-impact scoping/quota bypass in the gateway's message-freshness control, comparable in nature (not in blast radius) to the fee-schedule stage-skipping described in the source report — both stem from an incomplete/one-sided time-range validation on attacker-influenced input.

### Likelihood Explanation
Likelihood is Medium: the timestamp field is fully attacker-controlled at message-construction time (no server-side clock authority forces it), and the missing check requires no special privileges — any caller able to reach `HandleLegacyUserMessage` via the gateway's user-facing `web_api_trigger` path can exploit it.

### Recommendation
Change the staleness check to a bounded window around `time.Now()`, rejecting timestamps that are either older than `MaxAllowedMessageAgeSec` or newer than a small future-clock-skew tolerance, e.g.:
```go
now := time.Now().Unix()
if int64(payload.Timestamp) < now-int64(h.config.MaxAllowedMessageAgeSec) ||
   int64(payload.Timestamp) > now+int64(allowedClockSkewSec) {
    // reject
}
```

### Proof of Concept
1. A client constructs a `TriggerRequestPayload` for `MethodWebAPITrigger` and sets `Timestamp` to, e.g., `time.Now().Unix() + 10*365*24*3600` (10 years in the future), then signs the message as normal.
2. The client submits this message to the gateway repeatedly, at any point in the future.
3. In `HandleLegacyUserMessage`, the check at [2](#0-1)  never evaluates to true because `payload.Timestamp` is far larger than `time.Now().Unix()`, so the "stale message" rejection never triggers, and the message is forwarded to all DON members indefinitely, defeating the intended freshness bound.

Note: I was not able to fully trace how downstream DON-side capability handlers additionally validate `payload.Timestamp` for replay protection (that logic lives in `core/capabilities/webapi/trigger/trigger.go`, which I could not fully inspect within available tool calls); the finding above is scoped strictly to the gateway-side validation gap.

### Citations

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
