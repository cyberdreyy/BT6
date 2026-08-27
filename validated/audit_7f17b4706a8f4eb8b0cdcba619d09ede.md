### Title
Unvalidated `MaxAllowedMessageAgeSec` setting causes unsigned-integer underflow that permanently bricks the WebAPI trigger gateway handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`NewHandler` in `core/services/gateway/handlers/capabilities/handler.go` unmarshal-loads `HandlerConfig` (which contains `MaxAllowedMessageAgeSec uint`) and only applies defaults/validation to `CallbackMaxAgeSec`, `MaxSavedCallbacks`, and `CallbackPruneIntervalSec` — `MaxAllowedMessageAgeSec` is never range-checked. This value is later used in an unsigned subtraction inside `HandleLegacyUserMessage` that gates every incoming, unprivileged trigger request from the internet-facing gateway. This mirrors exactly the bug class in the nouns-builder report: numeric settings with no sane bounds ("reasonable range bounds... reverting where appropriate") that, once misconfigured, permanently break a critical operation (there, auctions/proposals; here, the gateway's web-api-trigger capability).

### Finding Description
`HandlerConfig.MaxAllowedMessageAgeSec` is loaded with zero validation: [1](#0-0) [2](#0-1) 

It is then used in `HandleLegacyUserMessage`, the path that processes every incoming, unprivileged user trigger request routed through the gateway: [3](#0-2) 

The critical line is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
```
Both operands are `uint`. If `MaxAllowedMessageAgeSec` is configured with a value larger than `uint(time.Now().Unix())` (current Unix epoch seconds, roughly 1.7–1.8 billion — an easy value to exceed by an operator mistakenly entering milliseconds, or any oversized number, exactly analogous to nouns-builder's "1000 years fits into `delay`" example), the subtraction underflows and wraps around to a value near `math.MaxUint`. The subsequent comparison `hugeWrappedValue > uint(payload.Timestamp)` then evaluates true for essentially all legitimate, non-stale timestamps, so every trigger request is rejected as "stale message" and returns an error response to the caller.

### Impact Explanation
Once this misconfiguration is deployed, the `web_api_trigger` path becomes permanently unusable for all unprivileged external senders hitting the gateway — every legitimate request is rejected with `"stale message"`, regardless of its actual timestamp. This is a denial-of-service on a customer/end-user-facing capability, directly analogous to "Auctions stalled" / "Proposals bricked" in the source report: a single bad, unbounded setting value takes down a core external-facing function for all users, with no way to recover except redeploying the job spec with a corrected value (there is no live self-heal path since the check runs on every request).

### Likelihood Explanation
Likelihood is low-to-moderate, matching the original report's "low likelihood, high impact" characterization: it requires an operator/job-spec author to set `maxAllowedMessageAgeSec` to an unusually large number (e.g., confusing seconds with milliseconds, or entering a very large "safety margin" value). There is no validation anywhere in the codebase (`NewHandler`, job spec parsing, or TOML default construction) that would catch or reject such a value before it reaches production, so a plausible operator typo/misunderstanding directly triggers full request rejection for all unprivileged senders.

### Recommendation
Add explicit bounds validation for `MaxAllowedMessageAgeSec` (and ideally use signed-safe duration arithmetic instead of unsigned subtraction) in `NewHandler`:
- Reject or clamp `MaxAllowedMessageAgeSec` values greater than a sane maximum (e.g., 24 hours) and reject 0 only if that's not the intended default-disable value.
- Replace the raw `uint` subtraction with a signed/`time.Duration`-based comparison, e.g. `time.Now().Unix() - int64(payload.Timestamp) > int64(h.config.MaxAllowedMessageAgeSec)`, so a misconfigured value cannot underflow and instead simply fails to filter stale messages (a much lower-impact failure mode) or is caught by validation at load time.

### Proof of Concept
1. Deploy a gateway job whose `web-api-capabilities` handler config sets `maxAllowedMessageAgeSec` to a value larger than the current Unix timestamp, e.g. `maxAllowedMessageAgeSec = 99999999999` (as configured via the job spec parsed in `deployment/cre/jobs/pkg/gateway_job.go`) [4](#0-3) .
2. `NewHandler` accepts this value unchanged since only `CallbackMaxAgeSec`, `MaxSavedCallbacks`, and `CallbackPruneIntervalSec` get default/bounds handling [5](#0-4) .
3. Any unprivileged external client sends a `web_api_trigger` request to the gateway's public endpoint with a valid, current `payload.Timestamp`.
4. In `HandleLegacyUserMessage`, `uint(time.Now().Unix()) - h.config.MaxAllowedMessageAgeSec` underflows to a value near `2^64-1` (or `2^32-1` depending on platform), so `... > uint(payload.Timestamp)` is always true, and the handler responds with a `"stale message"` `HandlerError` for every request regardless of freshness [6](#0-5) , permanently blocking the trigger capability for all callers until the job spec is corrected and redeployed.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L80-95)
```go
func NewHandler(handlerConfig json.RawMessage, donConfig *config.DONConfig, don handlers.DON, httpClient network.HTTPClient, lggr logger.Logger) (*handler, error) {
	var cfg HandlerConfig
	err := json.Unmarshal(handlerConfig, &cfg)
	if err != nil {
		return nil, err
	}
	if cfg.CallbackMaxAgeSec == 0 {
		cfg.CallbackMaxAgeSec = defaultCallbackMaxAgeSec
	}
	if cfg.MaxSavedCallbacks == 0 {
		cfg.MaxSavedCallbacks = defaultMaxSavedCallbacks
	}
	if cfg.CallbackPruneIntervalSec == 0 {
		cfg.CallbackPruneIntervalSec = defaultCallbackPruneIntervalSec
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

**File:** deployment/cre/jobs/pkg/gateway_job.go (L322-340)
```go
type webAPICapabilitiesHandlerConfig struct {
	MaxAllowedMessageAgeSec int                   `toml:"maxAllowedMessageAgeSec"`
	NodeRateLimiter         nodeRateLimiterConfig `toml:"NodeRateLimiter"`
}

func newDefaultWebAPICapabilitiesHandler() handler {
	return handler{
		Name: GatewayHandlerTypeWebAPICapabilities,
		Config: webAPICapabilitiesHandlerConfig{
			MaxAllowedMessageAgeSec: 1_000,
			NodeRateLimiter: nodeRateLimiterConfig{
				GlobalBurst:    10,
				GlobalRPS:      50,
				PerSenderBurst: 10,
				PerSenderRPS:   10,
			},
		},
	}
}
```
