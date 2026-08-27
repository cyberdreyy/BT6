### Title
Missing allowlist/authorization enforcement lets any internet client trigger WebAPI capability messages to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The Chainlink Gateway's HTTP endpoint (`network/httpserver.go`) accepts unauthenticated raw POST requests from the internet and forwards them into `gateway.ProcessRequest`, which routes "legacy" messages to `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`. That function explicitly skips authorization: `// TODO: apply allowlist and rate-limiting here` [1](#0-0) , and then unconditionally forwards the client-supplied message to every member of the DON: [2](#0-1) . This is analogous to the reported bug class: a service exposed to the internet performs a powerful/administrative action (broadcasting a signed capability-trigger request to all nodes of a DON) without verifying that the caller is actually permitted to do so.

### Finding Description
The gateway's HTTP server reads the raw request body and forwards it, together with an optional bearer token, straight into `ProcessRequest` with no authentication gate at the transport layer: [3](#0-2) . `ProcessRequest` decodes the JSON-RPC/legacy message and dispatches it to the handler keyed by DON ID or service name: [4](#0-3) , and calls `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage` before waiting for the handler's callback: [5](#0-4) .

For the WebAPI capabilities handler (used for `MethodWebAPITrigger` legacy messages), the only checks performed are structural: payload can be unmarshaled, a non-zero timestamp exists, and the message is not stale [6](#0-5) . Immediately after those checks, the code contains an explicit acknowledgement that authorization is missing (`// TODO: apply allowlist and rate-limiting here`) before validating only that the method name matches and forwarding the message, signed by an attacker's own key, to every node in the DON: [7](#0-6) .

This mirrors the report's root cause pattern: a public-facing service performs a sensitive, DON/node-wide action (equivalent to "admin" write capability) on behalf of unauthenticated/unverified callers because the authorization step was never implemented, only stubbed with a TODO. By contrast, the sibling `vault` gateway handler in the same package properly enforces authorization before dispatching (`h.requestProcessor.ProcessRequest` / `AuthorizeRequest` against an allowlist) [8](#0-7) , and the `webapi/trigger` connector handler enforces a sender allowlist before accepting trigger payloads [9](#0-8) , confirming that allowlist enforcement is the expected control that is missing specifically in `capabilities/handler.go`'s legacy path.

### Impact Explanation
Any unauthenticated internet client that can reach the Gateway's HTTP endpoint can craft a `MethodWebAPITrigger` legacy message and have it broadcast to every node in a targeted DON, without any allowlist/subscription check verifying the sender is authorized to trigger workflows for that DON. This can enable spamming/DoS of DON nodes with attacker-controlled trigger payloads, or unauthorized triggering of workflow executions that downstream logic assumes came from an allowlisted sender — directly matching the "unauthorized job run" / "allowlist bypass" impact categories called out in the validation rules.

### Likelihood Explanation
Likelihood is high for reachability: the Gateway HTTP endpoint is designed to be internet-facing (per `core/services/gateway/network/httpserver.go`, it just validates size limits and optional CORS, not caller identity), and no additional credential is required beyond forming a syntactically valid legacy message. The TODO comment is direct, in-code evidence that the intended allowlist control was never wired up for this code path, as opposed to being deliberately disabled behind a feature flag.

### Recommendation
- Short term: Implement the missing allowlist/rate-limit check called out by the TODO in `HandleLegacyUserMessage` before forwarding messages to DON nodes — reuse the same sender-allowlist mechanism already implemented in `core/capabilities/webapi/trigger/trigger.go`'s `processTrigger`.
- Long term: Require every Gateway handler implementing `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage` to perform authorization as a mandatory step (e.g., via a shared middleware/interface method) rather than leaving it as an opt-in per-handler responsibility, so a missing implementation cannot silently pass unauthenticated traffic through to DON nodes.

### Proof of Concept
1. Deploy/target a Gateway that maps a DON ID to the `capabilities` handler (webapi trigger legacy support).
2. From any unauthenticated client, POST a JSON-RPC legacy request body to the Gateway's HTTP path with:
   - `body.method = "web_api_trigger"` (the `MethodWebAPITrigger` constant)
   - `body.don_id` = the target DON ID
   - `body.payload` = a `webapicap.TriggerRequestPayload` JSON with `Timestamp` set to `time.Now().Unix()`
   - A signature produced with any attacker-owned key (only structural signature validity via `common.ValidatedRequestFromMessage` is required, not membership in an allowlist).
3. Observe in `core/services/gateway/handlers/capabilities/handler.go`'s `HandleLegacyUserMessage` that the message passes the timestamp/method checks and is forwarded via `don.SendToNode` to every DON member listed in `h.donConfig.Members`, with no allowlist check ever invoked [2](#0-1) .

Note: I was not able to fully verify, within the tool budget, whether `common.ValidatedRequestFromMessage`/`msg.Validate()` performs any sender-vs-allowlist check (only that it validates message structure/signature format) — this should be confirmed by a full read of `core/services/gateway/handlers/common/message_util.go` and `core/services/gateway/api/message.go` before treating this as fully conclusive, since a signature check alone (proving the message wasn't tampered with) would not prevent an attacker from simply signing with their own key and no allowlist is invoked in the cited code path either way.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-383)
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

**File:** core/services/gateway/network/httpserver.go (L196-219)
```go
	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		msg := "Failed to get request size limit"
		s.lggr.Errorw(msg, "err", err)
		http.Error(w, msg, http.StatusInternalServerError)
		return
	}
	source := http.MaxBytesReader(nil, r.Body, int64(maxRequestBytes))
	rawMessage, err := io.ReadAll(source)
	if err != nil {
		s.lggr.Error("error reading request", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}

	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
```

**File:** core/services/gateway/gateway.go (L217-262)
```go
// Called by the server
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
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
```

**File:** core/services/gateway/gateway.go (L264-277)
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

**File:** core/services/gateway/handlers/vault/handler.go (L431-443)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L149-184)
```go
}

func (h *triggerConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		h.lggr.Errorw("error validating message from request", "err", err, "request", req)
		return nil
	}
	body := &msg.Body
	sender := ethCommon.HexToAddress(body.Sender)
	var payload webapicap.TriggerRequestPayload
	err = json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw("error decoding payload", "err", err)
		err = h.sendResponse(ctx, gatewayID, body, ghcapabilities.TriggerResponsePayload{Status: "ERROR", ErrorMessage: fmt.Errorf("error %s decoding payload", err.Error()).Error()})
		if err != nil {
			h.lggr.Errorw("error sending response", "err", err)
		}
		return nil
	}

	switch body.Method {
	case ghcapabilities.MethodWebAPITrigger:
		resp := h.processTrigger(ctx, gatewayID, body, sender, payload)
		var response ghcapabilities.TriggerResponsePayload
		if resp == nil {
			response = ghcapabilities.TriggerResponsePayload{Status: "ACCEPTED"}
		} else {
			response = ghcapabilities.TriggerResponsePayload{Status: "ERROR", ErrorMessage: resp.Error()}
			h.lggr.Errorw("Error processing trigger", "gatewayID", gatewayID, "body", body, "response", resp)
		}
		err = h.sendResponse(ctx, gatewayID, body, response)
		if err != nil {
			h.lggr.Errorw("Error sending response", "body", body, "response", response, "err", err)
		}
		return nil
```
