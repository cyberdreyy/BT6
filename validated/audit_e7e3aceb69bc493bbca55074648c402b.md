## Title
Missing allowlist/rate-limiting enforcement on the WebAPI capability handler's legacy user-message entrypoint allows unauthenticated triggering of DON-wide requests - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The `UTB::receiveFromBridge` bug class is: a public/reachable entrypoint that is supposed to enforce the same fee/signature/access checks as its sibling entrypoints, but ships without them, letting an unprivileged caller reach privileged execution logic. The Chainlink gateway's `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` exhibits the same class of gap: it is the internet-facing entrypoint for legacy web API trigger requests, and while adjacent request paths (e.g. the vault `GatewayHandler.HandleGatewayMessage`) always run requests through an authorization/allowlist pipeline before dispatching to node-facing logic, this handler explicitly skips that step, marked by a literal `TODO: apply allowlist and rate-limiting here`.

### Finding Description
`handler.HandleLegacyUserMessage` is invoked from the gateway's public request path (`gateway.ProcessRequest` → `h.HandleLegacyUserMessage`) for any legacy-style request the gateway routes to this handler. [1](#0-0)  Before forwarding the request to every DON node, the function only checks payload decodability, presence of a timestamp, and message staleness — no allowlist or per-caller rate-limiting is applied, despite the comment explicitly flagging this omission: [2](#0-1) 

The request is then saved as a pending callback and broadcast to every member of the DON without any authorization gate: [3](#0-2) 

This is structurally the same class of bug as the UTB finding: a public entrypoint (`receiveFromBridge`) reachable without going through the modifier (`retrieveAndCollectFees`) that all its sibling entrypoints (`swapAndExecute`, `bridgeAndExecute`) enforce. Here, `HandleLegacyUserMessage` is a public-facing entrypoint reachable without the allowlist/rate-limit checks that other gateway handlers (e.g. the vault `GatewayHandler.HandleGatewayMessage`, which always calls `requestProcessor.ProcessRequest` for authorization before dispatch) enforce. [4](#0-3) 

### Impact Explanation
Without allowlist enforcement, any unauthenticated/unlisted caller reaching this method can trigger the `MethodWebAPITrigger` code path, causing the gateway to fan out the request to every node in the DON (`don.SendToNode` for each `h.donConfig.Members`). This can be abused for unauthorized job/workflow triggering and resource exhaustion (no rate limiting on this specific path), analogous to bypassing the fee/signature gate that legitimate callers of `swapAndExecute`/`bridgeAndExecute` are required to pass.

### Likelihood Explanation
The comment itself documents that the allowlist/rate-limiting was never implemented for this path ("apply allowlist and rate-limiting here"), and the function is reachable directly from the gateway's public HTTP request-processing entrypoint (`gateway.ProcessRequest`), making this highly likely to be reachable by any client capable of sending a legacy-formatted request that the gateway routes to this handler.

### Recommendation
Implement the missing allowlist and rate-limiting checks in `HandleLegacyUserMessage` before broadcasting to DON members, mirroring the authorization pipeline pattern used in `vault.GatewayHandler.HandleGatewayMessage` (i.e., invoke an authorizer/allowlist check and a rate limiter, and reject/short-circuit the request on failure, prior to any `don.SendToNode` calls).

### Proof of Concept
Not applicable in the strict on-chain PoC sense; the vulnerability is demonstrated by the code path itself: `gateway.ProcessRequest` → `HandleLegacyUserMessage` (missing allowlist/rate-limit check per explicit `TODO`) → broadcast to all `donConfig.Members` via `don.SendToNode`, with no gating logic present between message-staleness validation and DON-wide dispatch. [5](#0-4)

### Citations

**File:** core/services/gateway/gateway.go (L264-273)
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

**File:** core/capabilities/vault/gw_handler.go (L187-211)
```go
	switch req.Method {
	case vaulttypes.MethodSecretsCreate, vaulttypes.MethodSecretsUpdate:
		publicKey, pkErr := h.getMasterPublicKey(ctx)
		if pkErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pkErr)
			break
		}
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, publicKey)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}
```
