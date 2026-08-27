### Title
No per-sender quota enforcement on `HandleLegacyUserMessage` allows unmetered DON fan-out from a single unallowlisted sender - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` fans out every legacy user message to all DON members ` [1](#0-0) ` without any per-sender rate limiting or allowlist check, despite the presence of an explicit `TODO: apply allowlist and rate-limiting here` comment ` [2](#0-1) `. The only rate limiter in the file, `h.nodeRateLimiter`, is applied solely to outgoing node responses in `handleWebAPIOutgoingMessage`, not to inbound user requests ` [3](#0-2) `.

### Finding Description
The gateway's `ProcessRequest` decodes an incoming JSON-RPC request, validates message structure via `msg.Validate()` (signature/format checks only), and dispatches legacy requests directly to `h.HandleLegacyUserMessage` ` [4](#0-3) `. Inside `HandleLegacyUserMessage`, the checks performed are: payload decode success, non-zero timestamp, and message staleness ` [5](#0-4) `. None of these are per-sender identity/quota checks — they only validate message shape and freshness. Immediately after, the code explicitly marks the missing control with `// TODO: apply allowlist and rate-limiting here` before proceeding to send the request to every DON member via `don.SendToNode` in a loop ` [6](#0-5) `.

The only rate limiter instantiated in this handler, `h.nodeRateLimiter` (built from `HandlerConfig.NodeRateLimiter`), is exclusively invoked in `handleWebAPIOutgoingMessage`, which is called from `HandleNodeMessage` — i.e., it throttles DON *node* responses being relayed back through the gateway, not inbound end-user submissions ` [3](#0-2) ` ` [7](#0-6) `.

Because `msg.Body.MessageId` is attacker-controlled and only used for callback/dedup bookkeeping (`h.savedCallbacks`) rather than quota accounting ` [8](#0-7) `, an attacker can submit an unbounded number of distinct, validly-signed legacy requests from a single sender/EOA, each of which is unconditionally forwarded to every configured DON member.

### Impact Explanation
This matches Chainlink's "unmetered/free DON execution at scale" impact class: a single unallowlisted sender can force the DON to execute arbitrary workflow triggers repeatedly with no subscription/allowlist accounting, consuming DON compute, network, and subscriber-funded execution resources without payment or authorization tracking. This is a resource-exhaustion / free-execution vulnerability affecting the shared DON and its legitimate subscribers.

### Likelihood Explanation
Feasibility is high: the only precondition is unauthenticated network access to the gateway's legacy user-message endpoint (`ProcessRequest`), which is explicitly the gateway's public/user-facing entry point. No credentials, allowlist membership, or prior authorization are required — the attacker only needs to produce a validly-formed and signed message (per `msg.Validate()` and `common.ValidatedRequestFromMessage`) with a fresh timestamp and a unique `MessageId` for each submission. This is trivially scriptable and repeatable.

### Recommendation
Implement per-sender rate limiting/quota enforcement in `HandleLegacyUserMessage` before fan-out to DON members — e.g., reuse or add a `ratelimit.RateLimiter` (or the existing per-sender limiter pattern seen in `core/services/workflows/ratelimiter`) keyed by `msg.Body.Sender`/message signer address, and reject requests exceeding the configured quota with an appropriate JSON-RPC error (e.g., `api.RateLimitError` or similar) before the loop at line 417. Additionally, enforce the Functions/webapi allowlist and subscription accounting referenced by the existing TODO comment.

### Proof of Concept
Go handler-level integration test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a mock `handlers.DON` whose `SendToNode` is spied/counted.
2. Build N (e.g., 1000) distinct `api.Message` values, all with the same `Body.Sender` (unallowlisted address) but unique `Body.MessageId` and valid signatures/timestamps.
3. Invoke `h.HandleLegacyUserMessage(ctx, msg, callback)` for each message in a tight loop.
4. Assert: none of the calls return an allowlist/quota-rejection error, and `don.SendToNode` is called `N * len(donConfig.Members)` times — demonstrating unbounded fan-out from a single sender with no rejection due to quota exceedance.
5. Contrast with `handleWebAPIOutgoingMessage`'s node-side test, which does show rejections once `h.nodeRateLimiter.Allow(nodeAddr)` returns false, confirming the asymmetry: node responses are throttled, but user submissions are not.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-267)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
	start := time.Now()
	switch msg.Body.Method {
	case MethodWebAPITrigger:
		err = h.handleWebAPITriggerMessage(ctx, msg, nodeAddr)
	case MethodWebAPITarget, MethodComputeAction, MethodWorkflowSyncer:
		err = h.handleWebAPIOutgoingMessage(ctx, msg, nodeAddr)
	default:
		err = fmt.Errorf("unsupported method: %s", msg.Body.Method)
	}
	h.metrics.recordHandleDuration(ctx, time.Since(start), msg.Body.Method, err == nil)
	return err
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-419)
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
