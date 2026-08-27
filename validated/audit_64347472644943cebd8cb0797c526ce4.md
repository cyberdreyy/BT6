### Title
Missing future-timestamp bound in `HandleLegacyUserMessage` staleness check allows indefinitely-valid pre-signed requests - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The freshness check in `HandleLegacyUserMessage` only rejects messages whose `payload.Timestamp` is too old; it performs no upper-bound check against the current time, so a signed message with a `Timestamp` set arbitrarily far in the future passes the check unconditionally.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` validates `payload.Timestamp` with: [1](#0-0) 

This expression `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` only evaluates to true (and thus rejects the message) when the payload timestamp is older than `now - MaxAllowedMessageAgeSec`. There is no corresponding check such as `payload.Timestamp > now + someTolerance` to reject timestamps set in the future. A signer who crafts a `MethodWebAPITrigger` message with `Timestamp` set to, e.g., year 2100, will pass this check for the entire node lifetime, since `now` will never catch up enough to make the old-message branch trigger for the practical operational period of the node.

Note that this timestamp field is part of the payload body that is covered by the message signature validated in `common.ValidatedRequestFromMessage`/`ValidatedMessageFromResp` — i.e., the signature check does not enforce any timestamp semantics, it only proves who signed the bytes; the freshness logic is a separate, custom check that this code path fails to fully implement.

### Impact Explanation
This is a request-freshness/replay-window control gap, not an authentication or authorization bypass: the attacker must already be a valid holder of a signing key entitled to submit `MethodWebAPITrigger` requests to the gateway. The impact is that a single signed message can be captured and remain valid (never rejected as "stale") indefinitely, extending the useful replay window of the request. However, the code path does not do additional deduplication against replay within `HandleLegacyUserMessage` itself, so the practical exploitation still depends on the DON node's own handling of duplicate/triggered messages and whether the receiving nodes have separate anti-replay logic. Within the gateway itself, the impact is limited to weakening a freshness/replay-window invariant for already-privileged signers, rather than a new capability gained by an unprivileged attacker.

### Likelihood Explanation
Exploitation only requires an actor who already possesses a valid signing key for `MethodWebAPITrigger` messages — no additional privilege escalation is needed to trigger this specific defect. The construction is trivial (set `Timestamp` to a large future Unix value) and fully reproducible via a unit test calling `HandleLegacyUserMessage` directly with such a payload.

### Recommendation
Add an upper-bound check on `payload.Timestamp`, e.g.:
```go
now := uint(time.Now().Unix())
if now-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) || uint(payload.Timestamp) > now+h.config.MaxAllowedMessageAgeSec {
    // reject as stale/invalid
}
```
This bounds the timestamp to a symmetric (or configurable) window around the current time rather than only checking for staleness in the past.

### Proof of Concept
In `core/services/gateway/handlers/capabilities/handler_test.go`, add a table-driven test case for `HandleLegacyUserMessage`:
1. Construct a `webapicap.TriggerRequestPayload` with `Timestamp` set to `time.Now().Add(100*365*24*time.Hour).Unix()` (~year 2126).
2. Marshal into `msg.Body.Payload`, sign appropriately (reuse existing test helpers used for valid-message tests), and set `msg.Body.Method = MethodWebAPITrigger`.
3. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)`.
4. Assert that `callback.SendResponse` is **not** invoked with a "stale message" error response (i.e., the message proceeds to `don.SendToNode` for all DON members), demonstrating the future timestamp is accepted.

### Citations

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
