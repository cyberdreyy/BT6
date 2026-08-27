### Title
Unsafe `int64→uint` cast of client-controlled `Timestamp` bypasses stale-message check in gateway WebAPI trigger handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` in the gateway's capabilities handler validates message freshness by directly casting an attacker-supplied `int64` timestamp to `uint` and subtracting, mirroring the exact underflow/overflow bug class described in the report (`int256(a-b)`-style implicit casts that wrap instead of reverting/erroring).

### Finding Description
The gateway's capabilities `handler` accepts unauthenticated, internet-facing JSON messages from web API clients via `ProcessRequest` → `HandleLegacyUserMessage`. The payload is a `webapicap.TriggerRequestPayload` whose `Timestamp` field is a signed `int64` fully controlled by the caller: [1](#0-0) 

The staleness check performs an implicit unsafe cast/subtraction on unsigned integers, analogous to the reported `int256(a-b)` underflow pattern, but here in the opposite direction — casting a potentially negative `int64` into `uint`: [2](#0-1) 

Specifically:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
```
`payload.Timestamp` is a client-supplied `int64` (from JSON, unauthenticated at this layer beyond the message signature) and is cast directly to `uint`. If the client sends a negative timestamp (e.g. `-1`), `uint(payload.Timestamp)` wraps to a value near `2^64-1` (on 64-bit platforms). The left side (`now - maxAge`, always a small positive number relative to Unix epoch) will never exceed that wrapped value, so the "stale message" branch is never taken, and the message is treated as fresh regardless of its actual freshness.

`MaxAllowedMessageAgeSec` is declared as `uint`: [3](#0-2) 

### Impact Explanation
This bypasses the intended anti-replay/freshness control ("stale message") for `web_api_trigger` requests entering the DON via the gateway. An attacker who can produce a validly-signed message (message signing is a separate concern from the timestamp check) can force acceptance of a request that the freshness gate was designed to reject, regardless of how old or manipulated the timestamp is. Since this check is the only line of defense guarding message staleness before dispatch to `don.SendToNode` for all DON members, it undermines a designed anti-replay control at the internet-facing gateway boundary. Severity is bounded by the fact that per-topic sender allowlisting (`allowedSenders`) and per-sender rate limiting still apply downstream in `processTrigger`, so this is not a full authentication bypass, but it does defeat the freshness/anti-replay guarantee.

### Likelihood Explanation
Reachable directly from an unprivileged HTTP client via the gateway's `ProcessRequest` entrypoint with a crafted JSON payload containing a negative `timestamp` field; no special network position or node privilege is required. The only prerequisite is a validly signed message per `msg.Sign`/`msg.Validate`, which any client with knowledge of the expected signing key material for that DON/sender combination can produce (this is the same requirement as any other legitimate message).

### Recommendation
Avoid the implicit signed-to-unsigned cast. Validate that `payload.Timestamp` is non-negative (and within a sane bound) before comparison, and perform the age check entirely in signed arithmetic, e.g.:
```go
now := time.Now().Unix()
if payload.Timestamp < 0 || now-int64(h.config.MaxAllowedMessageAgeSec) > payload.Timestamp {
    // stale or invalid message
}
```
This matches the report's mitigation guidance of avoiding `int(a-b)`/`uint(x)` casts on values that can be negative and instead casting each operand explicitly before the arithmetic.

### Proof of Concept
1. Craft a `TriggerRequestPayload` JSON body with `"timestamp": -1` (or any negative int64) and a valid `topics`/`params` set.
2. Sign the enclosing `api.Message` with a key acceptable to `msg.Validate()`.
3. Submit via the gateway HTTP endpoint so it reaches `handler.HandleLegacyUserMessage`.
4. Observe that `uint(payload.Timestamp)` wraps to ~`2^64-1`, the condition `uint(now)-MaxAllowedMessageAgeSec > uint(payload.Timestamp)` evaluates false, and the "stale message" rejection branch at [4](#0-3) 
is never entered — the message proceeds to `don.SendToNode` for all DON members despite failing the intended freshness check.

### Citations

**File:** core/capabilities/webapi/webapicap/event_trigger_generated.go (L101-117)
```go
type TriggerRequestPayload struct {
	// Key-value pairs for the workflow engine, untranslated.
	Params TriggerRequestPayloadParams `json:"params" yaml:"params" mapstructure:"params"`

	// Timestamp of the event (unix time), needs to be within certain freshness to be
	// processed.
	Timestamp int64 `json:"timestamp" yaml:"timestamp" mapstructure:"timestamp"`

	// Topics corresponds to the JSON schema field "topics".
	Topics []string `json:"topics" yaml:"topics" mapstructure:"topics"`

	// Uniquely identifies generated event (scoped to trigger_id and sender).
	TriggerEventId string `json:"trigger_event_id" yaml:"trigger_event_id" mapstructure:"trigger_event_id"`

	// ID of the trigger corresponding to the capability ID.
	TriggerId string `json:"trigger_id" yaml:"trigger_id" mapstructure:"trigger_id"`
}
```

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
