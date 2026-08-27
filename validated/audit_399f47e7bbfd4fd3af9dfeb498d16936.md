### Title
Missing upper-bound timestamp validation in `HandleLegacyUserMessage` allows unbounded-future-dated messages to bypass the freshness/anti-replay window - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` only rejects messages whose `payload.Timestamp` is too old, but never rejects messages whose `payload.Timestamp` is in the future. An attacker who can produce a signed request (e.g., a valid gateway API credential holder) can set `Timestamp` far into the future (e.g., `now + 10^9`), causing the staleness check to always evaluate false and the message to be treated as "fresh" for an extremely long period, defeating the intended narrow freshness/anti-replay window.

### Finding Description
The freshness check is: [1](#0-0) 

The condition `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` rejects a message only when it is *too old* (i.e., `payload.Timestamp` is smaller than `now - MaxAllowedMessageAgeSec`). There is no corresponding check that `payload.Timestamp <= now` (or `now + small_skew`). Consequently, if an attacker sets `payload.Timestamp = now + 10^9`, the left side of the comparison stays far below `payload.Timestamp` for the entire duration until `now` catches up to that future value, so the "stale message" branch is never triggered and the message is accepted and forwarded to every DON member via `don.SendToNode`: [2](#0-1) 

Only a basic decode/zero check precedes it: [3](#0-2) 

There is no other upper-bound/clock-skew validation in this function, and no nonce or "already seen" replay-cache keyed independent of timestamp — the only anti-replay mechanism is this timestamp-freshness window, `savedCallbacks` (keyed on `msg.Body.MessageId`) which only governs the local one-shot callback delivery and is unrelated to timestamp acceptance, and gets pruned based on `CallbackMaxAgeSec`, not on `payload.Timestamp`: [4](#0-3) 

Because the freshness gate is one-sided, the intended "requests are bound to a valid narrow time window" invariant is violated for future-dated payloads: the acceptance window is effectively unbounded going forward.

### Impact Explanation
An attacker (any party able to produce a validly signed legacy user message reaching this handler) can pre-sign one or many `web_api_trigger` messages with far-future timestamps once, then submit/replay them against the gateway well beyond the intended `MaxAllowedMessageAgeSec` freshness window. Each accepted message is forwarded to every member of the DON (`don.SendToNode` loop over `h.donConfig.Members`), so this can be used to repeatedly trigger workflow execution on the DON outside of the freshness window the design intends, i.e. an extended replay/DoS surface against DON compute resources and workflow triggers. This most closely matches an "unauthorized job run" / weakened anti-replay control impact class, scoped to whatever the attacker was already authorized to trigger, but exercisable long after the intended validity window.

### Likelihood Explanation
Precondition is simply constructing a payload with `Timestamp` set arbitrarily far in the future — no special privilege beyond the ability to submit a signed legacy user message (which is the normal attacker capability assumed for gateway message senders) is required. The bug is deterministic and trivially reproducible: any payload with `Timestamp > now` bypasses the only freshness check in the function.

### Recommendation
Add an upper-bound check rejecting messages where `payload.Timestamp` exceeds `now + allowedClockSkew`, e.g.:
```go
now := uint(time.Now().Unix())
if now-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) || uint(payload.Timestamp) > now+allowedClockSkewSec {
    // reject as stale or not-yet-valid
}
```
This restores a bounded two-sided validity window consistent with the documented freshness intent.

### Proof of Concept
Go table test for `handler.HandleLegacyUserMessage`:
1. Construct `webapicap.TriggerRequestPayload{Timestamp: time.Now().Unix() + 1_000_000_000}` (approx. now + 10^9 seconds).
2. Build an `api.Message` with `Method: MethodWebAPITrigger` and this payload, with a valid signature/sender as required by `common.ValidatedRequestFromMessage`.
3. Call `h.HandleLegacyUserMessage(ctx, msg, callback)`.
4. Assert: no `"stale message"` error response is sent via `callback.SendResponse`, and `don.SendToNode` (mocked) is invoked for each DON member — proving the future-dated timestamp is accepted rather than rejected.
5. Contrast with a control case using `Timestamp = time.Now().Unix() - int64(h.config.MaxAllowedMessageAgeSec) - 1` to confirm the existing lower-bound rejection still works, highlighting the asymmetry.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-313)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-370)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-383)
```go
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
