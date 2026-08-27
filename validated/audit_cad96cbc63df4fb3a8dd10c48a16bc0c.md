### Title
Web API Trigger handler forwards workflow-trigger messages to all DON nodes with no allowlist/authorization check - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` in the gateway's WebAPIHandler accepts a `web_api_trigger` message from any client reaching the gateway's HTTP endpoint and immediately fans it out to every node in the DON, with the only checks being payload shape, timestamp freshness, and method name. There is no allowlist, sender authorization, or workflow-ownership check before dispatch, which is explicitly flagged by a `// TODO: apply allowlist and rate-limiting here` comment in the code.

### Finding Description
`HandleLegacyUserMessage` is the entry point invoked when an unprivileged client sends a JSON message to the gateway for the `web_api_trigger` method [1](#0-0) . After basic structural validation (payload decoding, timestamp presence, staleness check, and method match), it directly sends the request to every DON member without verifying that the sender is authorized to trigger the target workflow: [2](#0-1) 

The comment `// TODO: apply allowlist and rate-limiting here` sits right before the method check, and the test suite's own `TODO` notes "Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated" [3](#0-2) , confirming this gap is known but unresolved in this handler.

This is structurally analogous to the `addStrategy()` finding: a function that controls a critical, sensitive action (here, triggering workflow execution across an entire DON) is reachable without any access-control/allowlist gate, whereas comparable code paths elsewhere in the codebase (e.g., the Vault gateway handler and the v2 HTTP trigger handler) do enforce authorization before forwarding requests to nodes — via `AuthorizeRequest`/allowlist checks in `allow_list_based_auth.go` [4](#0-3)  and via `authorizeRequest`/JWT key checks in the v2 HTTP trigger handler [5](#0-4) .

### Impact Explanation
An unauthenticated actor able to reach the gateway's public endpoint can craft a `web_api_trigger` message and have it broadcast to every node of the target DON, invoking `MethodWebAPITrigger` processing on each node. Since this message ultimately triggers workflow execution logic on the DON, an attacker can potentially cause unauthorized workflow executions or spam the DON with attacker-controlled trigger events, without ever being verified as an authorized sender for that workflow/topic. This is comparable in spirit to redirecting a privileged flow (in the on-chain bug, minting/burning credit tokens; here, triggering DON-side workflow execution) toward attacker-chosen outcomes.

### Likelihood Explanation
The gap is explicit and acknowledged in the code (`TODO: apply allowlist and rate-limiting here`) and in tests, indicating the check is genuinely absent rather than implemented elsewhere and just not shown here in this handler. Reachability requires only network access to the gateway's HTTP-facing message intake, which is documented as internet-facing for legacy user messages. I was not able to fully trace whether an upstream layer (e.g., `httpserver.go`'s `handleRequest`/`ProcessRequest`, or `multihandler.go`) performs a generic allowlist check before dispatching to `HandleLegacyUserMessage` — this is uncertain because the tool budget was exhausted before I could inspect `multihandler.go` and `gateway.go`'s dispatch logic in full. If such an outer layer does enforce sender allowlisting for legacy messages, the practical exploitability would be reduced or eliminated; this should be verified before treating the finding as conclusively exploitable.

### Recommendation
Before dispatching to DON nodes in `HandleLegacyUserMessage`, verify that the message sender/signature is authorized for the specific workflow/topic being triggered (mirroring the allowlist-based or JWT-based authorization used in the Vault and v2 HTTP trigger handlers), and enforce rate limiting per sender, as the existing TODO comment indicates was intended.

### Proof of Concept
Not fully verifiable from static analysis alone. Based on the code path in `HandleLegacyUserMessage` [6](#0-5) , an attacker with network access to the gateway could send a signed `api.Message` with `Body.Method = "web_api_trigger"`, valid `TriggerRequestPayload` (topics, timestamp), and it would be forwarded to all DON members without any workflow-ownership or sender-authorization check — confirmation of exploitability depends on verifying that no allowlist check exists in the upstream `multihandler.go`/`gateway.go` dispatch path, which could not be completed within the available tool budget.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L34-77)
```go
func (r *allowListBasedAuth) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	r.lggr.Debugw("AllowListBasedAuth authorizing request", "method", req.Method, "requestID", req.ID)
	requestDigest, err := req.Digest()
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to create digest", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, err
	}
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to decode digest", "method", req.Method, "requestID", req.ID, "requestDigest", requestDigest, "error", err)
		return nil, err
	}
	requestDigestBytes32 := [32]byte(requestDigestBytes)
	if r.workflowRegistrySyncer == nil {
		r.lggr.Errorw("AllowListBasedAuth workflowRegistrySyncer is nil", "method", req.Method, "requestID", req.ID)
		return nil, errors.New("internal error: workflowRegistrySyncer is nil")
	}
	allowlistedRequest, allowedRequestsStrs, err := r.findAllowlistedItemWithRetry(ctx, req, requestDigest, requestDigestBytes32)
	if err != nil {
		return nil, err
	}
	if allowlistedRequest == nil {
		r.lggr.Debugw("AllowListBasedAuth request digest not allowlisted",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"allowedRequestsStrs", allowedRequestsStrs)
		return nil, errors.New("request not allowlisted")
	}

	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}

	digestKey := string(allowlistedRequest.RequestDigest[:])
	r.lggr.Debugw("AllowListBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", digestKey, "owner", allowlistedRequest.Owner.Hex(), "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
	return &AuthResult{
		workflowOwner: allowlistedRequest.Owner.Hex(),
		digest:        digestKey,
		expiresAt:     int64(allowlistedRequest.ExpiryTimestamp),
	}, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-369)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}
```
