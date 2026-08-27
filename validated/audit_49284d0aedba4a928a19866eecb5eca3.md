## Analysis Result

The reported bug class (a security-relevant step being silently skipped along one code path while being explicitly implemented on a sibling code path) has a concrete analog in the Chainlink Gateway's legacy Web API trigger ingestion path.

### Title
Missing allowlist/rate-limit enforcement before DON-wide broadcast in `HandleLegacyUserMessage` - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The Gateway's internet-facing `ProcessRequest` entrypoint routes "legacy" (DonId-bearing) user requests to `handler.HandleLegacyUserMessage`, which validates payload shape and timestamp freshness, then unconditionally fans the request out to every node in the DON — with an explicit `// TODO: apply allowlist and rate-limiting here` marking the gap. By contrast, the newer JSON-RPC path (`http_trigger_handler.go`'s `HandleUserTriggerRequest`) calls `authorizeRequest` and `checkRateLimit` before any node communication occurs.

### Finding Description
`gateway.ProcessRequest` [1](#0-0)  dispatches any request that carries a `DonId` to `HandleLegacyUserMessage` without the caller doing any authorization. Inside the handler, after only structural/timeliness checks, the code explicitly documents the missing control and proceeds to broadcast to all DON members regardless of who the sender is: [2](#0-1) 

This mirrors the external report's bug class: a security-relevant step (there, message-composition; here, allowlist/rate-limit enforcement) is implemented for one code path but silently absent for a sibling path that reaches the same trust boundary. The corresponding unit test file even documents the same uncertainty as an open TODO rather than a verified invariant: [3](#0-2) 

For comparison, the newer HTTP-trigger v2 path performs authorization and rate-limiting *before* touching any node: [4](#0-3) 

Per-workflow sender allowlisting and rate limiting do exist, but only downstream, inside each DON node's `triggerConnectorHandler.processTrigger`, which is invoked only after the Gateway has already broadcast the message to every node: [5](#0-4) 

### Impact Explanation
Because the Gateway itself performs no allowlist or rate-limit check on the legacy ingestion path, any unauthenticated internet client can force the Gateway to broadcast a message to **every node in the DON** for every request that merely satisfies basic shape/timestamp checks — even for senders that no workflow has allowlisted. The actual sender/topic authorization only happens per-node, per-registered-workflow, after the broadcast has already consumed Gateway→node bandwidth and each node's processing cycles. This creates an unthrottled fan-out amplification point usable for resource-exhaustion/DoS against the Gateway and the entire DON membership, i.e., a quota/allowlist bypass at the ingress tier described in the validation criteria.

### Likelihood Explanation
The path is reachable directly from an unauthenticated HTTP client hitting the Gateway's public user port with a well-formed legacy JSON-RPC-ish request containing a valid `DonId`, `Timestamp`, and `web_api_trigger` method — no credentials, session, or prior registration are required to reach the broadcast step. The explicit `TODO` comments in both the production code and its test suite indicate this is a known, unresolved gap rather than a documented accepted design tradeoff, increasing confidence this is unintentional.

### Recommendation
Add sender allowlist and rate-limit enforcement in `HandleLegacyUserMessage` (mirroring `authorizeRequest`/`checkRateLimit` in the v2 HTTP trigger handler) before the message is queued and broadcast to `h.donConfig.Members`, so that unauthorized/unlimited senders are rejected at the Gateway ingress rather than only after fan-out to every DON node.

### Proof of Concept
1. Send a POST to the Gateway's public HTTP endpoint with a legacy-format message: valid `DonId`, `Method: "web_api_trigger"`, a fresh `Timestamp`, and any `Sender` address that is not present in any workflow's `allowedSenders` map.
2. Observe in `gateway.ProcessRequest` that the request is routed to `HandleLegacyUserMessage` [6](#0-5) .
3. Observe that `HandleLegacyUserMessage` passes the timestamp/payload checks and proceeds straight to `don.SendToNode` for every DON member [7](#0-6)  without any allowlist/rate-limit call, even though the sender will ultimately be rejected only later, individually, by each node's `processTrigger` (per the `unauthorized Sender` error path).
4. Repeating this at high volume demonstrates unrestrained Gateway→all-nodes fan-out from an unauthenticated caller, since no gate exists prior to broadcast.

**Note:** I could not fully verify whether any earlier layer outside the files reviewed (e.g., HTTP middleware in `gw_net`/`httpServer`) imposes IP-based or global rate limiting in front of `ProcessRequest`; if such external throttling exists it would partially mitigate the DoS amplification, but it would not address the missing per-sender allowlist enforcement at the Gateway layer itself.

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
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

**File:** core/capabilities/webapi/trigger/trigger.go (L79-109)
```go
// processTrigger iterates over each topic, checking against senders and rateLimits, then starting event processing and responding
func (h *triggerConnectorHandler) processTrigger(ctx context.Context, gatewayID string, body *api.MessageBody, sender ethCommon.Address, payload webapicap.TriggerRequestPayload) error {
	// Pass on the payload with the expectation that it's in an acceptable format for the executor
	wrappedPayload, err := values.WrapMap(payload)
	if err != nil {
		return fmt.Errorf("error wrapping payload %w", err)
	}
	topics := payload.Topics

	// empty topics is error for V1
	if len(topics) == 0 {
		return errors.New("empty Workflow Topics")
	}

	// workflows that have matched topics
	matchedWorkflows := 0
	// workflows that have matched topic and passed all checks
	fullyMatchedWorkflows := 0
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
```
