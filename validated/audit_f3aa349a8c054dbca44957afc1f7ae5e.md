## Analog Found

### Title
Legacy WebAPI trigger message path forwards unauthenticated/unrate‑limited requests to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The Bittensor advisory describes `set_weights`/`commit_weights` extrinsics that are declared `Pays::No` (fee-free) while the per-neuron rate limit is enforced only deep inside the dispatch body, letting an unprivileged submitter flood blocks for free because the "gate" that is supposed to prevent abuse is not actually checked before the expensive work happens. The Chainlink Gateway's legacy WebAPI trigger handler exhibits the same structural flaw: `handler.HandleLegacyUserMessage` explicitly skips the allowlist/rate-limit check it was designed to have, and forwards every syntactically valid, signed message to every DON member and stores it in a shared in-memory map before any quota is enforced.

### Finding Description
`HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` processes inbound `web_api_trigger` messages from Gateway users. It performs payload decoding, timestamp/staleness checks, and method validation, then reaches an explicit gap:

```go
// TODO: apply allowlist and rate-limiting here
if msg.Body.Method != MethodWebAPITrigger {
``` [1](#0-0) 

No allowlist or per-sender/global rate limiter is consulted for this path even though the `handler` struct already owns a `nodeRateLimiter` used elsewhere for node-originated traffic [2](#0-1) . After the TODO'd check, the message is transformed and unconditionally: (1) stored in the shared `savedCallbacks` map keyed by `MessageId`, and (2) sent to *every* DON member:

```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()

for _, member := range h.donConfig.Members {
	err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
}
``` [3](#0-2) 

This mirrors the Bittensor pattern precisely: the "cost" of dispatch (fan-out to all N DON nodes, plus a map insertion) is paid up-front by the system for every request, while the control meant to gate that cost (allowlist/rate limiting) is either absent or, per the code's own comment, simply not implemented at this call site. The unit tests for this exact function acknowledge the gap: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [4](#0-3) .

By contrast, the newer trigger path (`triggerConnectorHandler.processTrigger` / `RegisterTrigger`) does enforce `allowedSenders` and a per-sender `rateLimiter.Allow(...)` check before dispatching events [5](#0-4) , and the V2 HTTP handlers enforce rate limits before expensive work as well (`checkRateLimit` called prior to sending to nodes) [6](#0-5) . The legacy handler in `handler.go` is the outlier where the enforcement point was left as a TODO but the expensive fan-out still executes for any well-formed signed message.

### Impact Explanation
Any party able to reach this Gateway handler with a validly signed (but otherwise arbitrary-content) `web_api_trigger` message can repeatedly flood all DON members with `SendToNode` calls and grow the `savedCallbacks` map (bounded only by a periodic pruning job, not by admission control), with no per-sender throttling gate at this call site. This is a quota/flood-control bypass on an unprivileged-facing dispatch path — analogous to fee-free, rate-limit-bypassed extrinsic flooding in the Bittensor report — and can degrade DON node availability/responsiveness and gateway memory footprint.

### Likelihood Explanation
The vulnerable code path requires only a validly signed message (signature validation is generic key-based, not the same as workflow-level authorization/allowlisting) reaching `HandleLegacyUserMessage`; the missing check is unconditional and explicitly marked as not-yet-implemented in both source and tests, so no special conditions or race timing are needed to trigger it — it is reachable on every request to this legacy method.

### Recommendation
Implement the allowlist and rate-limiting enforcement in `HandleLegacyUserMessage` before the message is stored in `savedCallbacks` and fanned out to DON members, consistent with the pattern already used in `triggerConnectorHandler.processTrigger` (per-sender allowlist + rate limiter check) and the V2 `httpTriggerHandler.checkRateLimit` (authorize + rate-limit check prior to dispatch). Alternatively, if this legacy path is deprecated, gate it behind a feature flag / reject it outright to remove the reachable attack surface.

### Proof of Concept
1. Craft a validly-signed `api.Message` with `Body.Method = MethodWebAPITrigger`, a fresh `MessageId`, and a `TriggerRequestPayload` with a non-zero `Timestamp` within `MaxAllowedMessageAgeSec`.
2. Call `handler.HandleLegacyUserMessage` (or the equivalent Gateway user-facing endpoint that routes to it) repeatedly in a tight loop, incrementing `MessageId` each time.
3. Observe that each call passes the decode/staleness/method checks and unconditionally executes `don.SendToNode` for every DON member and inserts an entry into `savedCallbacks`, with no rejection based on sender identity or request rate — confirming the missing allowlist/rate-limit gate on this dispatch path.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-61)
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L99-109)
```go
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
