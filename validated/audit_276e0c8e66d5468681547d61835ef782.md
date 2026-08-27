### Title
Unauthenticated/unallowlisted senders can trigger DON-wide `WebAPITrigger` broadcasts via `HandleLegacyUserMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` accepts any signed `TriggerRequestPayload` for `MethodWebAPITrigger`, performs only structural checks (payload decodability, non-zero/non-stale timestamp, correct method, request transformability), and then broadcasts the request to every DON member via `don.SendToNode` in a loop over `h.donConfig.Members`. There is no allowlist or per-sender authorization check gating who may trigger this broadcast, as explicitly marked by the unimplemented `TODO: apply allowlist and rate-limiting here` at line 384.

### Finding Description
The relevant code path is: [1](#0-0) 

The function performs these checks in order: JSON decode of the payload, `payload.Timestamp != 0`, staleness check against `h.config.MaxAllowedMessageAgeSec`, then the unimplemented allowlist/rate-limit TODO, method equality to `MethodWebAPITrigger`, and finally `common.ValidatedRequestFromMessage(msg)`, which only re-marshals the message into a JSON-RPC request without performing any sender authorization: [2](#0-1) 

After these checks pass, the handler unconditionally loops over all configured DON members and calls `don.SendToNode` for each: [3](#0-2) 

While `api.Message.Validate()` (invoked transitively as part of message construction/validation elsewhere in the gateway pipeline) verifies that a cryptographic signature is present and matches the claimed `Sender` field, it only proves the message wasn't forged by a third party impersonating a *different* claimed sender — it does not check whether that sender is a recognized/authorized workflow owner, API-token holder, or otherwise allowlisted identity. Any attacker who generates a fresh ECDSA keypair, signs a well-formed `TriggerRequestPayload` with a valid (non-stale) timestamp, and sets `Method` to `MethodWebAPITrigger` will pass every check in `HandleLegacyUserMessage` and cause the request to be broadcast to every DON member.

This is corroborated by the test suite itself, which explicitly acknowledges the gap is untested and unresolved: [4](#0-3) 

### Impact Explanation
An attacker with no node/API/operator credentials — merely an arbitrary ECDSA keypair — can force DON-wide compute by injecting a `MethodWebAPITrigger` request that gets forwarded to every DON member node. This falls under "unauthorized job run/unauthorized triggering of DON compute," since it lets an unrecognized identity initiate workflow execution requests processed by every node in the DON, wasting node resources, potentially triggering downstream external calls (e.g., HTTP capability actions) tied to attacker-supplied payload data, and denying capacity to legitimate workflow owners.

### Likelihood Explanation
The only precondition is the ability to generate an ECDSA keypair and sign a JSON payload to the required message format — no node role, API token, or DON membership is required. This is trivially reproducible and repeatable by any external actor able to reach the gateway's legacy message-handling entry point, matching the "unauthenticated/low-privileged gateway client" attacker model. Note: I was unable to fully trace, within available tool budget, whether an *outer* HTTP/dispatch layer (e.g., in `handler.go`, `gateway.go`, or `multihandler.go`) performs any additional sender-allowlist enforcement before invoking `HandleLegacyUserMessage`; the code, comments, and test suite examined show no such check reachable within `capabilities/handler.go` itself, and the developers' own `TODO` and test comment confirm the gap is real and acknowledged as unresolved at this layer.

### Recommendation
Implement the marked TODO: before invoking `don.SendToNode`, verify that the message's cryptographically-verified `Sender` address is present in a configured/registered allowlist (e.g., derived from the DON's authorized workflow owners or API-token-bound keys), and apply per-sender/global rate limiting (similar to `ratelimiter.RateLimiter` already used elsewhere in the codebase) to reject or throttle unknown senders prior to broadcast.

### Proof of Concept
Go handler-level integration test plan (extending `handler_test.go`):
1. Generate a fresh ECDSA private key not present in `nodes` fixture or any DON/workflow allowlist config.
2. Build a valid `TriggerRequestPayload` (`daily_price_update` workflow list) with current timestamp using `triggerRequest(t, freshKey, ...)`.
3. Instantiate `handler` with a mocked `don` (`handlermocks.DON`) and assert `mockDon.EXPECT().SendToNode(...)` is NOT set up / add a `.Maybe().Return(...)` and check invocation count.
4. Call `handler.HandleLegacyUserMessage(ctx, msg, cb)`.
5. Assert (expected to currently FAIL, proving the gap): `mockDon.AssertNotCalled(t, "SendToNode", mock.Anything, mock.Anything, mock.Anything)` — in the current implementation this assertion will fail because `SendToNode` is called once per DON member regardless of sender identity.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-421)
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
}
```

**File:** core/services/gateway/handlers/common/message_util.go (L82-105)
```go
// ValidatedRequestFromMessage converts a legacy Gateway Message to a JSON-RPC request
func ValidatedRequestFromMessage(msg *api.Message) (*jsonrpc.Request[json.RawMessage], error) {
	if msg == nil {
		return nil, errors.New("nil message")
	}
	if msg.Body.MessageId == "" {
		return nil, errors.New("message ID is empty")
	}
	if msg.Body.Method == "" {
		return nil, errors.New("method is empty")
	}
	params, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal message: %w", err)
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	return req, nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```
