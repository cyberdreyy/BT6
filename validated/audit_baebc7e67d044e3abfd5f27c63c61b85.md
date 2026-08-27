### Title
Missing upper-bound (future) timestamp validation allows indefinite replay of legacy trigger messages bypassing staleness check - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`HandleLegacyUserMessage` validates message freshness with a single one-sided check: [1](#0-0) 

This only rejects messages that are *too old* (`now - MaxAllowedMessageAgeSec > payload.Timestamp`). There is no corresponding check that `payload.Timestamp` is not unreasonably far in the future (e.g. `payload.Timestamp > now + someSkew`). `payload.Timestamp` is fully attacker-controlled input decoded straight from the JSON payload: [2](#0-1) 

Because the comparison is one-sided, a signed message carrying a `Timestamp` far in the future (e.g. `now + 10^9`) will never satisfy `now - MaxAllowedMessageAgeSec > payload.Timestamp`, no matter how much real time elapses. The "stale message" rejection branch therefore becomes permanently unreachable for such a message, and the exact same signed request/payload can be resubmitted indefinitely.

Unlike other paths in this codebase (`core/capabilities/vault/request_replay_guard.go`, `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`'s `jwtReplayCache`, and the JWT-based dedup in `http_trigger_handler_test.go`), the legacy `HandleLegacyUserMessage` path has no independent replay-nonce/digest cache — `savedCallbacks` is keyed by `MessageId` purely for routing the node's response back to the caller and is deleted/overwritten on each delivery, not used to reject already-seen message IDs. The timestamp window is thus the *only* anti-replay control on this legacy path, and it can be neutralized entirely by the sender simply picking an oversized `Timestamp` at signing time.

### Impact Explanation
This weakens the freshness/anti-replay guarantee on the legacy webapi trigger method (`MethodWebAPITrigger`). A captured or logged signed legacy message (e.g., leaked from logs, a compromised HTTP intermediary, or accidentally shared) can be replayed by anyone in possession of the raw message indefinitely, well beyond the intended `MaxAllowedMessageAgeSec` window, triggering repeated workflow execution requests to the DON under the original signer's identity. This maps to Chainlink's "Replay Attacks" / authentication-soundness impact class rather than fund loss or key disclosure, since it does not grant the attacker signing capability — it only extends the validity window of an already-signed message beyond its designed expiration.

### Likelihood Explanation
Exploitability requires the attacker to already possess one validly signed legacy trigger message with an inflated `Timestamp` — either because they are the original signer (low value, self-replay) or because they obtained a copy of someone else's signed message through some means (e.g., log/side-channel capture) not itself covered by network-layer/host-level exclusions. Given the check is purely code logic (no upper bound), the bypass is deterministic and 100% reproducible; no timing races or privileged access are needed to trigger the code path itself, only possession of a signed payload.

### Recommendation
Add a symmetric upper-bound check, e.g.:
```go
now := uint(time.Now().Unix())
if now - h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) || uint(payload.Timestamp) > now + allowedClockSkewSec {
    // reject as stale/invalid
}
```
Additionally, consider adding a short-lived message-ID/digest replay cache (similar to `RequestReplayGuard` used in the vault flow) for the legacy trigger handler so that even freshly-timestamped messages cannot be resubmitted more than once within the validity window.

### Proof of Concept
Go test plan (extends `handler_test.go` in `core/services/gateway/handlers/capabilities`):
1. Build `webapicap.TriggerRequestPayload{Timestamp: time.Now().Unix() + 1_000_000_000}` (far future) and marshal into an `api.Message` with `Method: MethodWebAPITrigger`, signed with a valid test private key (see `gwcommon.NewTestNodes`/`ValidatedRequestFromMessage` usage patterns in `handler_test.go`).
2. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)` immediately — assert no "stale message" error and that `don.SendToNode` is invoked (message accepted).
3. Advance/mock `time.Now()` (or sleep) beyond `MaxAllowedMessageAgeSec` (e.g., simulate `h.config.MaxAllowedMessageAgeSec + 1` seconds elapsed).
4. Call `handler.HandleLegacyUserMessage(ctx, msg, callback2)` again with the *same* signed message — assert it is **still accepted** (no "stale message" rejection logged, `don.SendToNode` called again), proving the staleness check never triggers for a future-dated `Timestamp` regardless of elapsed real time.
5. Contrast with a control case using `Timestamp = time.Now().Unix()` (not future) replayed after `MaxAllowedMessageAgeSec` — assert this **is** rejected with `"stale message"`, confirming the check only works when `Timestamp` is not artificially inflated.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-371)
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
