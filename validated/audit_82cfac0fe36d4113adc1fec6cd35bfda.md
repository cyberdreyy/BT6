### Title
Signed-to-unsigned timestamp cast bypasses gateway stale-message check via negative `Timestamp` value - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` in the gateway's WebAPI handler validates the freshness of an inbound `web_api_trigger` message by comparing an unsigned "now minus max-age" value against `uint(payload.Timestamp)`. `payload.Timestamp` originates from the attacker-controlled JSON payload of an unprivileged client/workflow request and is not range-validated before the unsafe conversion. Supplying a negative timestamp causes the `int64 → uint` conversion to wrap to a huge value, defeating the staleness comparison — structurally the same class of bug as the WarpSync `getFinalizationReward` underflow: an unchecked arithmetic/type-conversion operation on a value that can be outside the expected domain, producing a logically wrong (here: security-relevant) result.

### Finding Description
`HandleLegacyUserMessage` unmarshals the message payload into `webapicap.TriggerRequestPayload` and only special-cases `payload.Timestamp == 0`: [1](#0-0) 

It then performs the staleness check: [2](#0-1) 

`h.config.MaxAllowedMessageAgeSec` is declared as `uint`: [3](#0-2) 

The comparison is `uint(time.Now().Unix()) - h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)`. Since `payload.Timestamp` is attacker-supplied and only guarded against being exactly `0`, a negative value (e.g. `-1`) converted via `uint(payload.Timestamp)` wraps around to a value near `math.MaxUint64`. That makes the right-hand side of the comparison enormous, so the "stale message" branch is never taken regardless of how old (or fabricated) the timestamp actually is — the freshness check is effectively disabled for any negative timestamp.

This is directly analogous to the WarpSync bug class: an unguarded arithmetic/type operation on a value that can fall outside the range the code implicitly assumes (there: `block.timestamp < disputeWindowEnd` causing underflow; here: `payload.Timestamp < 0` causing wraparound) produces a value that silently subverts the intended check rather than erroring out.

### Impact Explanation
The staleness/freshness check exists to reject old or replayed `web_api_trigger` messages before they are dispatched to all DON members: [4](#0-3) 
(dispatch loop sending the request to all `donConfig.Members`)

By crafting a payload with a negative `Timestamp`, an unprivileged caller of the gateway's legacy user-message path can bypass this freshness gate and get otherwise-stale/replayed trigger requests forwarded to every DON node, undermining the intended anti-replay/staleness control on inbound gateway triggers. The downstream consequence is unauthorized/duplicate workflow trigger dispatch to the DON.

### Likelihood Explanation
The `Timestamp` field is fully attacker-controlled JSON input parsed directly from the incoming message with no lower-bound validation (only an equality check against `0`), so triggering the wraparound requires no special access — any client able to reach `HandleLegacyUserMessage` (the gateway's legacy `web_api_trigger` inbound path) can set `Timestamp` to a negative integer.

### Recommendation
Validate `payload.Timestamp` as non-negative (and reject clearly invalid values) before using it, and avoid mixing signed and unsigned arithmetic for this comparison — e.g., compare using `int64` throughout: `if payload.Timestamp < time.Now().Unix()-int64(h.config.MaxAllowedMessageAgeSec) { ... stale ... }`, and explicitly reject `payload.Timestamp <= 0`.

### Proof of Concept
1. Craft a legacy gateway message with `Body.Method = "web_api_trigger"` and payload JSON containing `"timestamp": -1` (or any negative int64).
2. Send it to the gateway's `HandleLegacyUserMessage` path.
3. The `payload.Timestamp == 0` check passes (since it's `-1`, not `0`).
4. `uint(payload.Timestamp)` wraps to `18446744073709551615`, making `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec > uint(payload.Timestamp)` always false.
5. The "stale message" rejection is skipped, and the request is forwarded to all DON members despite carrying an invalid/stale timestamp.

**Note on verification limits:** I could not confirm the exact declared type of `webapicap.TriggerRequestPayload.Timestamp` (e.g. whether it's `int64`) because the generated file `core/capabilities/webapi/webapicap/event_trigger_generated.go` content was not retrievable via the available search tools before the tool budget was exhausted. The presence of the explicit `uint(payload.Timestamp)` cast at line 372 strongly implies the field is a signed integer type, which is the basis for this finding; a Devin session with full repo access should confirm this field's type before treating this as fully confirmed.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L63-70)
```go
type HandlerConfig struct {
	NodeRateLimiter         ratelimit.RateLimiterConfig `json:"nodeRateLimiter"`
	MaxAllowedMessageAgeSec uint                        `json:"maxAllowedMessageAgeSec"`

	CallbackMaxAgeSec        int `json:"callbackMaxAgeSec"`
	MaxSavedCallbacks        int `json:"maxSavedCallbacks"`
	CallbackPruneIntervalSec int `json:"callbackPruneIntervalSec"`
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-370)
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
