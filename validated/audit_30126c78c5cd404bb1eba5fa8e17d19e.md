## Analog Found: Unsafe integer conversion/subtraction bypasses stale-message check in Gateway `HandleLegacyUserMessage`

### Title
Unchecked signed-to-unsigned conversion in stale-message check allows replay/bypass of message-freshness validation - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The reported bug class is "unvalidated params combined with unsafe/unchecked math causing under/overflow." An analogous pattern exists in chainlink's internet-facing Gateway handler for WebAPI trigger messages: an externally-supplied, unvalidated `Timestamp` field is used directly in unsigned subtraction/comparison to decide whether a message is "stale," without bounds-checking the conversion from a signed value to `uint`.

### Finding Description
`handler.HandleLegacyUserMessage` is the entry point the Gateway invokes for legacy (DON-ID-addressed) requests coming from unprivileged, internet-facing clients via `gateway.ProcessRequest`: [1](#0-0) [2](#0-1) 

The staleness check is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    ...  "stale message"
}
``` [3](#0-2) 

`payload.Timestamp` is decoded from client-controlled JSON (`webapicap.TriggerRequestPayload`) with no upper/lower bound validation beyond a zero-check: [4](#0-3) 

`HandlerConfig.MaxAllowedMessageAgeSec` and the local time are both mixed into an unsigned (`uint`) expression. Because `payload.Timestamp` is only checked for `== 0`, an attacker can supply any other numeric value (including a negative one, which JSON permits) for this field. When such a value is converted with `uint(payload.Timestamp)`, Go performs a two's-complement wraparound, producing an arbitrarily large `uint`. This makes the comparison `now - maxAge > uint(payload.Timestamp)` evaluate to `false` regardless of the real elapsed time, defeating the intended freshness/anti-replay check. This mirrors the reported bug class exactly: an attacker-influenced numeric parameter (`payload.Timestamp`, analogous to `_price`) is fed into unchecked/unsafe integer arithmetic (the unsigned subtraction/conversion, analogous to the `unchecked { ... }` block) without adequate validation of its range.

### Impact Explanation
This check exists specifically to reject stale WebAPI trigger messages/replays from unprivileged clients before they are dispatched to all DON members: [5](#0-4) 
Bypassing it allows an unprivileged, internet-facing caller to have arbitrarily old (or maliciously crafted) trigger messages accepted and forwarded to every DON node as if fresh, undermining the freshness/anti-replay guarantee the Gateway is supposed to enforce for this unprivileged pathway. This falls under "unauthorized job run" per the validation criteria, since it can enable acceptance/replay of trigger payloads that should have been rejected.

### Likelihood Explanation
The check is reachable directly from any unprivileged network client hitting the Gateway's legacy JSON-RPC endpoint with `MethodWebAPITrigger`/DON-ID-addressed payloads; no authentication beyond the generic gateway allowlist/signature validation on the message envelope is required to reach this code path, and the vulnerable field (`Timestamp`) is fully attacker-controlled JSON input.

### Recommendation
- Validate `payload.Timestamp` is non-negative and within a sane bound (e.g., not in the far future/past) before using it in arithmetic.
- Perform the comparison using signed 64-bit arithmetic (`int64`) throughout, or use `time.Unix(payload.Timestamp, 0)` and `time.Since(...)` comparisons instead of manual unsigned subtraction.
- Add an explicit overflow/underflow-safe helper (the codebase already has one pattern for this, e.g. `uint64SeqDeltaToInt64` in `core/capabilities/vault/request_lifecycle_tracker.go`) and reuse a similar safe-delta approach here.

### Proof of Concept
1. Attacker sends a legacy Gateway request with method `web_api_trigger`, DON ID set, and a payload containing `"timestamp": -1` (or any negative int64).
2. `json.Unmarshal` succeeds since `Timestamp` accepts signed JSON numbers.
3. The `payload.Timestamp == 0` check passes (value is `-1`, not `0`).
4. `uint(payload.Timestamp)` wraps to `math.MaxUint - 0`ish (≈18446744073709551615 on 64-bit), which is far greater than `uint(time.Now().Unix()) - h.config.MaxAllowedMessageAgeSec`.
5. The stale-message branch is skipped, and the message is forwarded to every DON node as if it were fresh, despite carrying an invalid/attacker-chosen timestamp. [6](#0-5)

### Citations

**File:** core/services/gateway/gateway.go (L264-276)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-420)
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
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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
