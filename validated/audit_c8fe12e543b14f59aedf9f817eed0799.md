Based on my investigation, the strongest analog in this codebase to the yEarn oracle's `block.timestamp`-manipulation bug is the stale-message check in the Gateway's `web-api-capabilities` handler, which trusts a caller-supplied timestamp field for a security-relevant freshness/replay decision.

### Title
Client-controlled `payload.Timestamp` used for stale-message rejection allows staleness/replay-window bypass - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` decides whether an incoming message is "stale" (and therefore rejected) purely by comparing `time.Now()` against a `Timestamp` field taken directly from the unauthenticated/untrusted request payload, with no server-side capture of arrival time and no cryptographic binding verified at this stage of the check.

### Finding Description
The handler unmarshals the payload and reads `payload.Timestamp` from `TriggerRequestPayload`, then performs the freshness check entirely against that self-reported value: [1](#0-0) 

The comparison logic itself:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    ...stale message...
}
``` [2](#0-1) 

This is structurally the same bug class as the reported `_valid`/`block.timestamp` issue: a security decision (accept vs. reject as stale) is made against a timestamp value that is fully controlled by the party being checked, rather than an independently, verifiably-derived value (e.g., the gateway's own receipt time, or a timestamp cryptographically bound and range-checked as part of signature verification). Just as a miner can shift `block.timestamp` to game `_valid`, a client crafting the JSON payload can set `Timestamp` to any past or future value to game this check — e.g., setting a timestamp far in the future to always pass the staleness check regardless of actual message age, defeating the purpose of `MaxAllowedMessageAgeSec` (which exists specifically to bound message freshness/replay windows, as evidenced by the dedicated config knob and test coverage for "stale message" rejection). [3](#0-2) [4](#0-3) 

I was not able to fully confirm from the indexed code whether `Timestamp` is part of a signed envelope that is verified for integrity *before* this check runs (i.e., whether tampering with `Timestamp` would also invalidate a signature check elsewhere in the pipeline). The `TriggerRequestPayload`/`Timestamp` field's exact schema and any signature-binding logic live in generated files (`event_trigger_generated.go`, `trigger_builders_generated.go`) that I was only able to partially inspect. Given index size limits, some file contents may not be fully available — a Devin session with full repository access would be needed to trace the complete signature-verification path for `TriggerRequestPayload` and conclusively determine whether `Timestamp` is protected from tampering prior to this staleness check.

### Impact Explanation
If `Timestamp` is not cryptographically bound/verified before this check, an unprivileged caller can bypass the staleness/replay-window protection (`MaxAllowedMessageAgeSec`) entirely by supplying an arbitrary future timestamp, allowing replay of old/expired trigger requests to the DON through the gateway, or bypassing the intended freshness guarantee relied upon by downstream node-side logic.

### Likelihood Explanation
This code path (`HandleLegacyUserMessage`) is reachable directly from a webhook/HTTP client submitting a JSON-RPC message to the gateway's legacy user message endpoint — no special privilege is required to submit a message, only a valid method (`web_api_trigger`) and JSON payload. The precondition (whether `Timestamp` integrity is independently verified elsewhere) remains unconfirmed from what I could inspect.

### Recommendation
Do not rely solely on a caller-supplied `Timestamp` for freshness decisions. Either: (1) use the gateway's own received-at wall-clock time for the staleness check, or (2) if a client-supplied timestamp is required (e.g., for signed request replay protection), ensure it is included in the signed payload and that the signature is verified *before* the staleness comparison, and bound to a small, explicit tolerance window (analogous to not setting `periodSize`/tolerance too small, per the external report's recommendation) rather than solely gating on `MaxAllowedMessageAgeSec` against an unverified field.

### Proof of Concept
A client sends a `web_api_trigger` JSON-RPC message with `payload.Timestamp` set to `time.Now().Unix() + <large future offset>`. The comparison `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec > uint(payload.Timestamp)` evaluates false regardless of when the message was actually created or how long it has been replayed, causing the "stale message" rejection path to never trigger for that message, as directly implemented at: [5](#0-4)

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-383)
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L286-302)
```go
	t.Run("sad case stale message", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		}, r)
	})
```
