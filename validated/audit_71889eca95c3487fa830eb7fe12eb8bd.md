The strongest analog to the "commented-out whitelist check" bug class in this codebase is the explicit `TODO` gap in `HandleLegacyUserMessage`, the legacy internet-facing entry point for `web_api_trigger` requests in the Capabilities gateway handler.

### Title
Missing allowlist and rate-limiting enforcement in `HandleLegacyUserMessage` allows unprivileged senders to flood all DON nodes - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
Just as the Anchor bridge left whitelist `require()` checks commented out (allowing any token to reach the bridge), the Chainlink gateway's `handler.HandleLegacyUserMessage` explicitly documents a missing allowlist/rate-limit check with a `// TODO: apply allowlist and rate-limiting here` comment, then proceeds to broadcast the request to every DON member node regardless.

### Finding Description
`HandleLegacyUserMessage` is invoked directly from `gateway.ProcessRequest`, the public HTTP entry point that decodes and dispatches any external, unauthenticated-beyond-signature request to a handler [1](#0-0) . Inside the handler, only payload decoding, timestamp/staleness checks, and method-name matching are performed before the request is fanned out to every member of the DON [2](#0-1) . Immediately before the method check, the code contains:
```go
// TODO: apply allowlist and rate-limiting here
if msg.Body.Method != MethodWebAPITrigger {
``` [3](#0-2) 

Unlike `handleWebAPIOutgoingMessage`, which does apply `h.nodeRateLimiter.Allow(nodeAddr)` for node-originated traffic [4](#0-3) , there is no equivalent limiter or allowlist check applied to the gateway-level, user-originated legacy trigger path before it is forwarded to `don.SendToNode` for every DON member [5](#0-4) . Sender/topic allowlisting and per-sender rate limiting are only enforced later, at the DON node side in `triggerConnectorHandler.processTrigger` [6](#0-5)  — meaning the gateway itself performs no filtering before consuming resources and relaying to the network of nodes.

### Impact Explanation
An unprivileged external client can submit an arbitrary number of validly-formed (but otherwise unauthorized) legacy trigger messages to the gateway's public HTTP endpoint. Because the gateway-level allowlist/rate-limit is admittedly not implemented (per the TODO), each such message is broadcast to every DON member node before any per-sender or per-workflow check occurs, unlike the newer v2 HTTP trigger handler path which enforces JWT/allowlist authorization before dispatch. This allows resource exhaustion / spam amplification across an entire DON from a single unprivileged caller, since the cost of rejecting an unauthorized sender is paid by every node in the DON rather than by the gateway up front.

### Likelihood Explanation
The legacy path is reachable directly from any external HTTP client through `gateway.ProcessRequest` whenever a request lacks a DON-scoped JSON-RPC service name/handler mapping, and it requires only a validly signed message (which any actor with a keypair can produce) — no allowlisting, node registration, or bridge/route-specific privilege is needed to invoke it. The comment `// TODO: apply allowlist and rate-limiting here` explicitly confirms the enforcement gap is a known, currently-open omission rather than requiring deep exploitation.

### Recommendation
Implement the allowlist and rate-limiting checks in `HandleLegacyUserMessage` before fanning requests out to DON members, mirroring the sender/topic allowlist and per-sender rate limiter already used on the node side (`allowedSenders`, `rateLimiter.Allow`) and the node-rate-limiter pattern already used for the outgoing message path in the same file.

### Proof of Concept
1. Craft a validly signed `api.Message` with `Method: MethodWebAPITrigger` and a fresh, non-stale `Timestamp`, for an arbitrary sender key not present in any workflow's `allowedSenders`.
2. Submit it repeatedly to the gateway's public endpoint so it is routed via `gateway.ProcessRequest` to `handler.HandleLegacyUserMessage`.
3. Observe that, absent any gateway-side allowlist/rate-limit (per the TODO), each request is forwarded via `don.SendToNode` to every member of the DON [5](#0-4) , before the per-sender allowlist/rate limiter in `processTrigger` on the node side finally rejects it — demonstrating that the DON-wide broadcast cost is incurred for every unauthorized request.

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

**File:** core/capabilities/webapi/trigger/trigger.go (L97-109)
```go
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
