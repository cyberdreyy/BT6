### Title
Unsafe unsigned-integer subtraction in Gateway legacy trigger staleness check allows bypass of message freshness validation - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The `HandleLegacyUserMessage` function in the internet-facing Gateway's WebAPI capability handler validates the freshness of an inbound trigger request using `uint` subtraction on two values, one of which (`payload.Timestamp`) is fully attacker-controlled input parsed straight from the untrusted user-submitted JSON payload. As in the reported Convexity `OptionsContract` bug, performing subtraction on unsigned values without checking for underflow can flip the comparison logic and silently defeat the intended time-window check.

### Finding Description
`HandleLegacyUserMessage` is reachable from any unauthenticated client hitting the Gateway's public HTTP endpoint (`gateway.ProcessRequest` → handler dispatch) and decodes `payload.Timestamp` directly from the request body: [1](#0-0) 

It performs only a zero check on `payload.Timestamp`, then evaluates staleness with unchecked `uint` arithmetic: [2](#0-1) 

The check `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` mirrors the root cause of the referenced report: an unchecked subtraction feeding a comparison meant to gate a time-based trust decision. Because `payload.Timestamp` is attacker-supplied and only validated against the exact value `0` (not against negative or absurdly large values), a crafted value can produce a `uint(payload.Timestamp)` that wraps around (if the underlying JSON type is signed and a negative number is accepted) or is otherwise chosen to always exceed `uint(now) - MaxAllowedMessageAgeSec`, causing the "stale message" branch to never trigger regardless of the message's real age.

`HandlerConfig.MaxAllowedMessageAgeSec` is a fixed, node-operator-configured `uint` value: [3](#0-2) 

### Impact Explanation
This staleness check is the only anti-replay/freshness gate applied to legacy WebAPI trigger messages before they are broadcast to every DON member node: [4](#0-3) 

An unprivileged external client can bypass the intended "reject messages older than `MaxAllowedMessageAgeSec`" guarantee by choosing a timestamp value that defeats the unsigned comparison, allowing forwarding of messages the Gateway was designed to reject as stale/replayed to all DON nodes. This weakens the freshness/anti-replay protection at the Gateway ingress boundary, which is one of the few validations applied before fan-out to the DON.

### Likelihood Explanation
The vulnerable code path is reachable from any unauthenticated HTTP client able to submit a legacy `web_api_trigger` request to the Gateway (`gateway.ProcessRequest`), requiring no special privileges — only crafting the JSON `Timestamp` field. This matches the "unprivileged client request" criterion for a strongly reachable analog.

### Recommendation
Replace the raw `uint` subtraction/comparison with a signed, bounds-checked comparison (e.g., compare `time.Now().Unix()` and `payload.Timestamp` as `int64` and explicitly reject negative, zero, or future-dated timestamps before computing any difference), avoiding any subtraction that can underflow when operands are attacker-controlled.

### Proof of Concept
1. An unauthenticated client sends a legacy Gateway request with `method: "web_api_trigger"` and a JSON payload containing a `Timestamp` value crafted to defeat the unsigned comparison (e.g., a value whose `uint` cast is larger than `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`, including negative values if the underlying type is signed).
2. `HandleLegacyUserMessage` passes the zero-check and the staleness check reachable at [5](#0-4)  evaluates to `false` regardless of the message's actual age.
3. The message is forwarded unmodified to every DON node via `don.SendToNode`, even though it should have been rejected as stale.

Note: I could not conclusively verify the exact Go type (`int64` vs `uint64`) of the `Timestamp` field in `webapicap.TriggerRequestPayload` within the available index (only partial matches were found in `core/capabilities/webapi/webapicap/event_trigger_generated.go`), so the negative-value wraparound scenario specifically depends on that field being a signed type; regardless of sign, the unchecked `uint` subtraction/comparison pattern itself is the confirmed root-cause parallel to the reported bug class.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-358)
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
