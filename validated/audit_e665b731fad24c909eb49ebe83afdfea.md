Confirmed analog: `HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` reaches the network/DON-forwarding path via the public, unauthenticated user-facing gateway HTTP endpoint (`gateway.ProcessRequest` → `h.HandleLegacyUserMessage`) with an explicit `// TODO: apply allowlist and rate-limiting here` comment before forwarding the request to DON nodes, unlike sibling code paths (e.g. vault's `HandleJSONRPCUserMessage`/`GatewayVaultRequestProcessor`, and the new `v2` HTTP handler) which enforce authorization/allowlist checks before doing any work.

### Title
Missing allowlist/rate-limit validation on legacy WebAPI trigger user messages before forwarding to DON - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The gateway's WebAPI capability handler forwards unauthenticated, un-allowlisted user requests directly to all DON member nodes on the legacy `web_api_trigger` path.

### Finding Description
`gateway.ProcessRequest` routes any request with a populated `msg.Body.DonId` to `h.HandleLegacyUserMessage` [1](#0-0) . Inside `HandleLegacyUserMessage`, the code validates payload shape, timestamp freshness, and method name, but explicitly skips authorization/allowlisting with a `// TODO: apply allowlist and rate-limiting here` comment, then immediately fans the raw request out to every DON member node: [2](#0-1) . This is structurally analogous to the EvolvingProteus bug class: a shared invariant/validation check (`_checkBalances()` there, allowlist/authorization here) is applied on some code paths (`_swap`, `_lpTokenSpecified` there; vault's `GatewayVaultRequestProcessor.authorizeAndStamp` and the new `v2` HTTP handler here [3](#0-2) ) but is missing on a comparable path (`depositGivenInputAmount`/`withdrawGivenOutputAmount` there; legacy `web_api_trigger` here).

### Impact Explanation
Any unauthenticated client hitting the gateway's user HTTP port can submit a `web_api_trigger` message that is unconditionally relayed to every node in the DON, since no allowlist, subscription, or per-owner authorization check is performed on this path (only payload shape/timestamp/method-name checks). This allows request impersonation/spoofing of workflow triggers and can be used to flood DON nodes, bypassing the allowlist controls otherwise enforced in the vault and v2 HTTP handler code paths.

### Likelihood Explanation
High: the code path is reachable by any unprivileged HTTP client of the gateway's public-facing user endpoint with no additional preconditions, and the missing-check location is explicitly marked by a `TODO` comment left in production code, confirming the gap is real rather than covered elsewhere in this file.

### Recommendation
Implement and enforce an allowlist/authorization check (and rate limiting keyed by sender) in `HandleLegacyUserMessage` before forwarding to `don.SendToNode`, mirroring the authorization pipeline used by the vault gateway handler (`GatewayVaultRequestProcessor`/`Authorizer`) or the v2 HTTP handler, rather than leaving it as an outstanding TODO.

### Proof of Concept
1. Send a raw JSON-RPC-style legacy request to the gateway's user port with `Body.DonId` set to a valid DON ID and `Body.Method` = `web_api_trigger`, with a fresh `Timestamp` and valid payload shape, from an arbitrary/unregistered sender address.
2. Observe in `HandleLegacyUserMessage` [4](#0-3)  that no allowlist/authorization check runs (per the TODO at line 384) and the request is transformed via `common.ValidatedRequestFromMessage` and sent to all DON members via `don.SendToNode` for every configured member address.
3. Compare against the vault path, where `AuthorizeRequest`/`GatewayVaultRequestProcessor.authorizeAndStamp` is required before any request reaches the DON, confirming the legacy WebAPI path is the outlier lacking this control.

### Citations

**File:** core/services/gateway/gateway.go (L250-273)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}

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

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L255-265)
```go
}

func (p *GatewayVaultRequestProcessor) validationError(req *jsonrpc.Request[json.RawMessage], err error) error {
	invalidErr := InvalidVaultParamsError{Method: req.Method, Err: err}
	if IsInvalidVaultParamsError(err) {
		p.lggr.Warnw("gateway vault request validation failed", "method", req.Method, "requestID", req.ID, "error", invalidErr)
	} else {
		p.lggr.Errorw("failed to validate gateway vault request before authorization", "method", req.Method, "requestID", req.ID, "error", invalidErr)
	}
	return invalidErr
}
```
