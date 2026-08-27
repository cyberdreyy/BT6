### Title
Unauthenticated workflow trigger dispatch via legacy gateway path bypasses allowlist and rate-limiting - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Sherlock report describes a design where a documented security guarantee (rewards can only be distributed after passing through the accounted-for `receive` path) is silently bypassed because an alternate, undocumented delivery path (direct balance credit) exists and is never checked. The Chainlink Gateway has an analogous split: the modern HTTP Trigger v2 handler enforces JWT authentication, workflow-owner authorization, and per-owner rate limiting before dispatching a trigger to a DON, but the legacy `web_api_trigger` code path — still wired up and reachable from the same unauthenticated internet-facing HTTP endpoint — has no such checks, as flagged by its own `// TODO: apply allowlist and rate-limiting here` comment.

### Finding Description
The gateway's HTTP endpoint accepts any POST request without requiring authentication; it only optionally extracts a bearer token if present [1](#0-0) . The request is forwarded to `gateway.ProcessRequest`, which branches on whether the decoded message carries a `DonId`. If it does, the request is treated as a "legacy request," routed purely by DON ID lookup, and dispatched via `HandleLegacyUserMessage` with no gateway-level authorization step [2](#0-1) .

`HandleLegacyUserMessage` in the capabilities handler validates message shape (payload decoding, non-zero timestamp, message freshness) and the `web_api_trigger` method name, but explicitly skips authorization/allowlisting, as marked by its own TODO comment, before forwarding the raw request to every member of the DON: [3](#0-2) 

This is in stark contrast to the newer v2 HTTP Trigger handler, which explicitly performs workflow resolution, JWT/key-based `authorizeRequest`, and `checkRateLimit` before dispatching to the DON, and which explicitly rejects legacy messages instead of silently forwarding them: [4](#0-3) [5](#0-4) 

Just as the Rio contract only accounted for value arriving through its `receive` function and silently dropped value delivered via the alternate (direct-credit) path, the gateway's real security controls (auth, allowlist, rate-limit) were only implemented on the new v2 dispatch path, while the older/legacy dispatch path — which is still registered in `Methods()` and reachable from the same unauthenticated HTTP listener — never received the equivalent controls.

### Impact Explanation
An unauthenticated, unprivileged internet client can submit a `web_api_trigger` message specifying an arbitrary target `DonId` and have it broadcast to every member node of that DON, without passing the workflow-owner authorization or per-owner rate limiting that the equivalent modern path enforces. This is an allowlist/authorization-control bypass on a capability-trigger dispatch path, and also removes the rate-limiting protection meant to prevent abuse/flooding of DON nodes with attacker-controlled trigger payloads.

### Likelihood Explanation
The legacy path is not disabled: `Methods()` still advertises `MethodWebAPITrigger`, `HandleLegacyUserMessage` is exercised by tests, and the HTTP server performs no authentication before calling `ProcessRequest`. Any DON configured to use this handler (as opposed to only the newer `v2` handler that explicitly refuses legacy messages) remains exposed to any client capable of reaching the gateway's public HTTP port.

### Recommendation
Either remove the legacy `web_api_trigger` dispatch path entirely (mirroring the v2 handler's explicit rejection of legacy messages), or implement the allowlist/authorization and rate-limiting checks referenced by the TODO comment before forwarding requests to DON members in `HandleLegacyUserMessage`.

### Proof of Concept
1. Stand up a gateway configured with the legacy `capabilities.handler` (not the v2 HTTP handler) for a target DON.
2. Send an unauthenticated POST to the gateway's configured path with a JSON-RPC body containing a `DonId` matching the target DON and `Method: "web_api_trigger"`, a valid `Timestamp`, and an arbitrary `Payload`.
3. Observe that `gateway.ProcessRequest` treats it as `isLegacyRequest`, and `HandleLegacyUserMessage` forwards the request to every node in `h.donConfig.Members` without any owner/allowlist check or rate limiting, as shown at [6](#0-5) , compared to the mandatory `authorizeRequest`/`checkRateLimit` calls in the v2 path [7](#0-6) .

### Citations

**File:** core/services/gateway/network/httpserver.go (L211-219)
```go
	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
```

**File:** core/services/gateway/gateway.go (L250-269)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L387-389)
```go
func (h *gatewayHandler) HandleLegacyUserMessage(context.Context, *api.Message, handlers.Callback) error {
	return errors.New("HTTP capability gateway handler does not support legacy messages")
}
```
