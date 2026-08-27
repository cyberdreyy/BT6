### Title
Signed-to-unsigned integer conversion in gateway stale-message check allows replay-protection bypass - (`core/services/gateway/handlers/capabilities/handler.go`)

### Summary
`HandleLegacyUserMessage` in the gateway's WebAPI capability handler validates message freshness using an unsigned-integer subtraction/comparison built from an attacker-supplied `payload.Timestamp` field that is explicitly cast to `uint`. This mirrors the reported analog bug class: converting a value that can be negative (or attacker-controlled) into an unsigned type causes wraparound rather than a proper bounds/`abs` check, which can silently invalidate the intended validation logic.

### Finding Description
`HandleLegacyUserMessage` decodes an untrusted `webapicap.TriggerRequestPayload` from the message body (`json.Unmarshal(body.Payload, &payload)`), then performs the staleness check: [1](#0-0) 

The check is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
```
`payload.Timestamp` originates entirely from the untrusted `Request` sent over the internet-facing gateway path (`ProcessRequest` → `HandleLegacyUserMessage`), and `HandlerConfig.MaxAllowedMessageAgeSec` is a configured `uint`: [2](#0-1) 

Because `uint(payload.Timestamp)` is an unchecked conversion (exactly the same bug class described in the external report — casting a value that can be negative to an unsigned type), a caller who supplies a negative or otherwise out-of-range `Timestamp` value causes the conversion to wrap to a very large `uint64`, which will always satisfy `> ...` being false (i.e., the message will never be judged "stale"), defeating the freshness/anti-replay check entirely.

### Impact Explanation
This check is the only replay/staleness protection applied to legacy WebAPI trigger messages before they are broadcast to every DON member node (`don.SendToNode` for each `h.donConfig.Members`): [3](#0-2) 
An unprivileged external caller reaching the gateway's `ProcessRequest` entrypoint could bypass the freshness check by choosing a timestamp value that produces integer wraparound, undermining the intended anti-replay/staleness guarantee, and enabling replay of stale/attacker-crafted trigger payloads to all DON nodes behind this gateway path.

### Likelihood Explanation
This is only fully confirmed as `High` in likelihood if `payload.Timestamp`'s declared type permits negative or excessively large values (e.g. a signed integer). I was not able to conclusively locate and read the exact field declaration/type of `Timestamp` on `webapicap.TriggerRequestPayload` within the available tool budget (only indirect references were found in `event_trigger_generated.go` and `trigger_builders_generated.go`, which I could not fully inspect). If `Timestamp` is unmarshaled as a JSON number into a Go `int64` field (typical for Unix timestamps), an attacker fully controls this value via the request body and the bug is directly triggerable pre-authentication over the network-facing gateway. This should be verified before treating the finding as certain.

### Recommendation
Avoid casting a potentially-negative or attacker-controlled value directly to `uint`. Validate `payload.Timestamp` is within a sane range (non-negative, not too far in the future) before comparison, and perform the staleness comparison using signed arithmetic (e.g. `time.Now().Unix() - int64(h.config.MaxAllowedMessageAgeSec) > payload.Timestamp`) or clamp/reject invalid values explicitly, mirroring the `abs`/bounds-check fix recommended in the original report.

### Proof of Concept
1. Confirm the concrete Go type of `webapicap.TriggerRequestPayload.Timestamp` (likely `int64`) via the repository (not confirmed in this session due to tool budget).
2. Send a legacy gateway message (`msg.Body.Method == MethodWebAPITrigger`) with `payload.Timestamp` set to a negative integer, e.g. `-1`.
3. `uint(payload.Timestamp)` wraps to `18446744073709551615` (max uint64), which is always `>=` the left-hand side `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`, so the "stale message" branch at [1](#0-0)  is never taken, and the forged/stale message proceeds to be dispatched to all DON nodes.

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
