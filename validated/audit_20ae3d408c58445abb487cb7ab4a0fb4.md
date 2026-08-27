### Title
Legacy WebAPITrigger requests bypass allowlist/rate-limiting per explicit TODO - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`HandleLegacyUserMessage` decodes the incoming payload, validates the timestamp isn't stale, and then immediately checks `msg.Body.Method != MethodWebAPITrigger` before forwarding the request to all DON members via `don.SendToNode`. Directly above this method check sits an explicit `// TODO: apply allowlist and rate-limiting here` comment, and no allowlist or per-sender/per-workflow rate-limiting call exists anywhere in the function body between message parsing and dispatch to `don.SendToNode`. [1](#0-0) 

By contrast, the rate limiting that does exist in this handler (`h.nodeRateLimiter`) is only applied in `handleWebAPIOutgoingMessage`, which governs outbound node-initiated HTTP requests, not inbound user-initiated trigger requests. [2](#0-1) 

I was not able to fully trace the outer HTTP/gateway transport layer (`core/services/gateway/network/httpserver.go` and the top-level dispatch in `gateway.go`) to confirm whether signature verification or any allowlist check occurs *before* `HandleLegacyUserMessage` is invoked — my searches for `Signature`/`verify` in `httpserver.go` returned no matches, and I could not open `gateway.go`/`multihandler.go` in this session due to tool-call limits. The v2 HTTP trigger path (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`) does have an explicit `authorizeRequest` (JWT-based) and `checkRateLimit` step before dispatch, confirming that the codebase's newer design intentionally adds these controls — which is consistent with the older/legacy handler's TODO indicating the equivalent controls are known to be missing there. [3](#0-2) 

### Impact Explanation
If confirmed unreachable-by-upstream-checks (which I could not fully verify), this would allow any caller reaching this handler to trigger DON execution of a `web_api_trigger` job for any configured DON member without being on an allowlist and without being subject to per-sender/global rate limits — i.e., free/unauthorized DON compute consumption. This maps to an "unauthorized job run" / "allowlist bypass" impact class, but is scoped by whatever transport-level authentication gates access to this legacy gateway endpoint (which I could not confirm one way or the other).

### Likelihood Explanation
Uncertain/unverifiable from available context. The TODO comment textually confirms the allowlist/rate-limit gap exists inside `HandleLegacyUserMessage`, but I could not confirm the absence of gating at the HTTP/gateway ingress layer (signature verification, DON-membership checks in `gateway.go`/`network/httpserver.go`) that might already restrict which senders' messages ever reach this function. Without that confirmation, I cannot assert this is exploitable end-to-end by an unprivileged HTTP client.

### Recommendation
Trace and document the full ingress path for legacy user messages (`network/httpserver.go` → `gateway.go` → `multihandler.go` → `HandleLegacyUserMessage`) to determine whether an upstream check already enforces allowlist/authentication. If not, implement the allowlist and rate-limiting called out in the TODO before dispatching to `don.SendToNode`, mirroring the pattern already implemented in the v2 `httpTriggerHandler.authorizeRequest`/`checkRateLimit`.

### Proof of Concept
Given the incomplete trace of the ingress path, a definitive PoC cannot be constructed with confidence in this session. A recommended handler-level integration test:
1. Construct a `handler` via `NewHandler` with a `donConfig` containing DON members and no allowlist configuration.
2. Build an `api.Message` with `Method: MethodWebAPITrigger`, a valid non-stale `Timestamp`, and a valid `TriggerRequestPayload`, from a sender not present in any allowlist/subscription list.
3. Call `h.HandleLegacyUserMessage(ctx, msg, callback)` directly (bypassing any assumed upstream transport auth) and assert `don.SendToNode` is invoked for DON members despite the sender having no allowlist entry — this would confirm the function-level gap.
4. As a follow-up, a Devin session with filesystem/terminal access should read `core/services/gateway/gateway.go` and `core/services/gateway/network/httpserver.go` in full to confirm/deny whether allowlist or signature checks occur before this call, since that determines whether this is truly attacker-reachable by an unprivileged client or is mitigated by outer-layer controls.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-420)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-396)
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

func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
```
