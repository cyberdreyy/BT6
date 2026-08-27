### Title
Missing allowlist and rate-limiting enforcement in legacy Gateway user-message handler allows unauthenticated resource-consumption / DON-flooding - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `HandleLegacyUserMessage` function, which processes every user request submitted through the internet-facing Gateway to the `web-api-capabilities` DON handler, explicitly skips allowlist and rate-limiting checks before broadcasting the request to every node in the DON.

### Finding Description
`HandleLegacyUserMessage` validates payload decoding, timestamp presence, and message staleness, but then contains an explicit TODO acknowledging the missing control, immediately followed by dispatch to every DON member: [1](#0-0) 
After that check, the handler stores a callback and fans the request out to all configured DON members with no allowlist or per-sender/global rate limit applied: [2](#0-1) 

This is inconsistent with the node-originated path in the same handler (`handleWebAPIOutgoingMessage`), which does enforce `h.nodeRateLimiter.Allow(nodeAddr)` before doing any work: [3](#0-2) 

The handler is reached from the multi-handler dispatcher, which routes any legacy user message straight to the target handler's `HandleLegacyUserMessage` based solely on `msg.Body.Method`, with no allowlist/rate-limit gate at the dispatch layer either: [4](#0-3) 

The `handler` struct does hold a `nodeRateLimiter` (`ratelimit.RateLimiter`) which is used only for node→gateway traffic, not for the user→gateway legacy path: [5](#0-4) 

This is conceptually the same bug class as the reported MonadBFT issue: a resource-limiting mechanism exists (byte size checks/proposal limits in the analog; allowlist + rate limiter in Chainlink) but is not actually applied at the point where an unprivileged/external actor's request consumes the protected resource (block gas budget in the analog; DON compute/bandwidth budget here), letting the actor consume far more of the shared resource than the control was designed to permit.

### Impact Explanation
An unauthenticated/unprivileged HTTP client that reaches the Gateway's user-facing endpoint (which routes into this handler for `MethodWebAPITrigger` legacy messages) can submit an unbounded volume of requests that are broadcast to every node of the target DON, without being subject to the allowlist or the `NodeRateLimiter` that governs other paths. This enables resource-exhaustion / DoS of DON nodes and bypasses the access-control gate ("allowlist bypass") that the system is designed to enforce for gateway traffic, analogous to how the reported bug allowed disproportionate resource consumption relative to the intended limiting mechanism.

### Likelihood Explanation
The vulnerable code path is reached directly from the Gateway's externally-facing message ingestion, requiring no privileged credentials — only a validly-formed legacy JSON message with method `web_api_trigger` and a non-zero, non-stale timestamp. The missing check is explicitly flagged by the developers' own TODO comment, confirming it is a known gap rather than a misread of intended behavior.

### Recommendation
Apply the same allowlist and rate-limiting (`nodeRateLimiter`/an equivalent user-facing limiter) checks in `HandleLegacyUserMessage` before storing the callback and fanning the request out to DON members, mirroring the enforcement already present in `handleWebAPIOutgoingMessage`.

### Proof of Concept
Not applicable — this was identified via static code review of `core/services/gateway/handlers/capabilities/handler.go`; no PoC harness for the Gateway HTTP ingress was inspected in this pass, and full request-path wiring in `core/services/gateway/gateway.go` (referenced but not fully read) was not completely verified within the available context. This should be confirmed by tracing the HTTP ingress in `gateway.go` to `HandleLegacyUserMessage` before treating this as fully proven end-to-end.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-60)
```go
type handler struct {
	services.StateMachine
	config          HandlerConfig
	don             handlers.DON
	donConfig       *config.DONConfig
	savedCallbacks  map[string]*savedCallback
	mu              sync.Mutex
	lggr            logger.Logger
	httpClient      network.HTTPClient
	nodeRateLimiter *ratelimit.RateLimiter
	wg              sync.WaitGroup
	stopCh          services.StopChan
	metrics         *metrics
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
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

**File:** core/services/gateway/multihandler.go (L53-60)
```go
func (m *multiHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	h, err := m.getHandler(msg.Body.Method)
	if err != nil {
		return fmt.Errorf("failed to get handler for method %s: %w", msg.Body.Method, err)
	}

	return h.HandleLegacyUserMessage(ctx, msg, callback)
}
```
