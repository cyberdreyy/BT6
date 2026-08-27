### Title
Missing sender allowlist in HandleLegacyUserMessage allows any unregistered keypair to trigger DON capability execution on all nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`(*handler).HandleLegacyUserMessage` validates message structure, staleness, and signature integrity via `common.ValidatedRequestFromMessage`, but performs no check that `msg.Body.Sender` is an authorized/registered workflow owner before forwarding the request to every DON member via `don.SendToNode`. The code explicitly marks this gap with `// TODO: apply allowlist and rate-limiting here` at line 384.

### Finding Description
The legacy gateway request flow is: `gateway.ProcessRequest` (core/services/gateway/gateway.go:264-273) decodes and structurally validates the message (`msg.Validate()`), looks up the DON handler by `DonId`, and calls `h.HandleLegacyUserMessage(ctx, msg, callback)`. Inside `HandleLegacyUserMessage` [1](#0-0) , the code checks: payload decoding, `payload.Timestamp == 0`, and message staleness. It then hits the explicit TODO comment and unconditionally proceeds to method validation and `common.ValidatedRequestFromMessage(msg)` [2](#0-1) , which only verifies the cryptographic well-formedness/signature-to-body binding of the message, not whether `Sender` is an authorized/registered entity (workflow owner, subscriber, etc.). Finally, the request is saved as a callback and broadcast to **every** DON member: [3](#0-2) .

Since `msg.Sign(privateKey)` only requires possession of any ECDSA keypair (no registration with the DON/gateway is required to produce a validly-signed message), any unauthenticated actor can craft a `web_api_trigger` message, sign it with a fresh key, and have it dispatched to all DON nodes, consuming their compute (fetching from the node's callback framework and invoking capability execution) without being tied to any authorized workflow/capability owner.

This is corroborated by the existing test suite, which contains a comment acknowledging the gap is unresolved: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [4](#0-3) , and no test in that file asserts a sender-based rejection for `HandleLegacyUserMessage`.

Notably, the newer v2 implementation of this same trigger flow, `httpTriggerHandler.HandleUserTriggerRequest`, explicitly calls `h.authorizeRequest(ctx, workflowID, req, callback)` and `h.checkRateLimit(...)` before dispatching [5](#0-4) , confirming that authorization/rate-limiting is the intended design for this class of request but was never implemented in the legacy v1 path being audited.

### Impact Explanation
An unregistered/unauthenticated EOA holding any ECDSA keypair can force every member node of a target DON to process a `web_api_trigger` message and invoke the corresponding capability/workflow execution path, without being tied to any authorized subscription. This maps to Chainlink's "unauthorized job run" / DoS-via-resource-consumption impact class: an attacker can flood DON nodes with signed-but-unauthorized trigger messages, consuming node compute/HTTP egress resources reserved for legitimate subscribers, and impersonate the "any sender is treated equally" trust model since no allowlist differentiates authorized subscribers from arbitrary keys.

### Likelihood Explanation
Preconditions are minimal: the attacker needs no credentials beyond generating an ECDSA keypair (free, instant, unlimited) and reaching the gateway's public HTTP/JSON-RPC endpoint that maps to `gateway.ProcessRequest`. The message only needs to pass structural/staleness/signature-well-formedness checks — all of which are self-satisfiable by the attacker since they control both the keypair and the message content. This is fully repeatable and scriptable (generate new key, sign, POST, repeat), and there is no rate limit or allowlist gating this at the `HandleLegacyUserMessage` layer (the `nodeRateLimiter` in this handler applies only to node-originated outgoing messages via `handleWebAPIOutgoingMessage`, not to inbound legacy user messages).

### Recommendation
Implement the allowlist/authorization check called out in the TODO before dispatching to `don.SendToNode`: verify `msg.Body.Sender` against a registered set of authorized workflow/capability owners (e.g., via workflow registry syncer, similar to `allowListBasedAuth.AuthorizeRequest` used elsewhere) and apply per-sender rate limiting, mirroring the pattern already implemented in `v2/http_trigger_handler.go`'s `authorizeRequest`/`checkRateLimit` calls. Reject unauthorized senders with an `api.HandlerError`/unauthorized response before touching `savedCallbacks` or `don.SendToNode`.

### Proof of Concept
Go unit test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Set up `handler` via existing `setupHandler(t)` helper with a `don` mock DON expecting `SendToNode` calls.
2. Generate a **fresh, never-registered** ECDSA private key (not part of `nodes` fixture and not present in any allowlist/config).
3. Build a valid `web_api_trigger` `TriggerRequestPayload` (valid `Timestamp`, valid `Params`), sign the resulting `api.Message` with the fresh key using `msg.Sign(freshKey)`.
4. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)`.
5. Assert:
   - `don.SendToNode` was invoked once per member in `h.donConfig.Members` (`don.AssertExpectations(t)` / `don.AssertNumberOfCalls(t, "SendToNode", len(members))`).
   - No error/`UnauthorizedError`/`HandlerError` response was returned via `callback.Wait(ctx)` rejecting the sender.
   - This demonstrates dispatch occurs despite the signer having no allowlist entry, config registration, or prior relationship with the DON.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-357)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-409)
```go
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```
