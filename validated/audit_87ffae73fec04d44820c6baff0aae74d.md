Based on the investigation, there is a concrete unprivileged-client analog in the gateway's legacy WebAPI trigger handler.

### Title
Missing allowlist and rate-limit validation on unprivileged user-submitted WebAPI trigger messages - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The external report's root cause is that the Pheasant Network Bridge accepted relayer-submitted evidence without validating a required field (the block number) before acting on it, allowing unslashable/fraudulent claims. The analogous pattern in this repo is `handler.HandleLegacyUserMessage`, the gateway entry point for unprivileged HTTP clients submitting `web_api_trigger` requests: it validates payload shape, staleness, and method name, but explicitly skips allowlist and rate-limiting validation — marked with a literal `// TODO: apply allowlist and rate-limiting here` comment — before broadcasting the request to every DON member node.

### Finding Description
`HandleLegacyUserMessage` is reachable from an unprivileged/external HTTP client hitting the gateway (see the companion `HandleReceiveHTTPMessageFromClient` test path). It performs several checks — JSON payload decoding, a non-zero timestamp check, and a staleness check comparing `payload.Timestamp` to `h.config.MaxAllowedMessageAgeSec` — then explicitly states allowlist/rate-limit enforcement is not yet implemented: [1](#0-0) 

Immediately after this comment, the code proceeds to save a callback and fan the raw request out to every member of the DON: [2](#0-1) 

This is inconsistent with the sibling outbound path `handleWebAPIOutgoingMessage`, which does enforce a per-node rate limiter (`h.nodeRateLimiter.Allow(nodeAddr)`) before processing node-originated requests: [3](#0-2) 

The handler's own test suite acknowledges the gap is unresolved: "TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated." [4](#0-3) 

### Impact Explanation
Because there is no allowlist check on the sender/caller and no rate limiting on this ingress path, any unprivileged client able to reach the gateway's legacy HTTP-message endpoint can submit an unbounded volume of `web_api_trigger` messages that are broadcast to every node in the DON, and can potentially trigger workflow executions or resource consumption without being a permitted/allowlisted sender. This mirrors the bug class of "missing validation before acting on externally supplied, security-relevant data" from the report — here, the missing validation is sender authorization/quota rather than a block number, but the consequence is the same category: unauthorized/abusive requests are accepted and acted upon because a validation step that should gate acceptance is absent.

### Likelihood Explanation
The code path is reachable directly by any external client capable of sending a message to the gateway's legacy user-message handler; no operator, peer, or mocked-only preconditions are required. The gap is not theoretical — it is explicitly flagged as an unresolved TODO in both the production code and its test file, indicating the intended validation was designed but never implemented.

### Recommendation
Implement the allowlist check (verifying the message sender/signer is authorized for the target DON/workflow) and apply a rate limiter (analogous to `h.nodeRateLimiter` used in `handleWebAPIOutgoingMessage`) to `HandleLegacyUserMessage` before it saves the callback and forwards the request to DON members, matching the enforcement already present on the outbound path.

### Proof of Concept
1. An external client crafts a `web_api_trigger` message with a valid (non-zero, non-stale) `payload.Timestamp` and the correct `MethodWebAPITrigger` method.
2. The client sends this message to the gateway's HTTP ingress that ultimately calls `HandleLegacyUserMessage` (as exercised by `TestHandlerReceiveHTTPMessageFromClient` in `handler_test.go`).
3. Because the allowlist/rate-limit checks are not implemented (per the `TODO` at `handler.go:384`), the message passes straight through to `don.SendToNode` for every DON member, regardless of whether the sender is authorized or how many requests it has already sent. [5](#0-4)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-420)
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```
