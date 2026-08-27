### Title
Node-scoped shared rate limiter in `handleWebAPIOutgoingMessage` allows one workflow/user to exhaust the quota and deny service to all other subscribers routed through the same DON node - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.handleWebAPIOutgoingMessage` enforces outgoing HTTP fetch quota solely via `h.nodeRateLimiter.Allow(nodeAddr)`, i.e. keyed by the DON node's address rather than by the originating workflow/subscriber. Since many independent users' `web_api_target`/`compute_action` workflows are routed and aggregated through the same physical DON node, one workflow that bursts enough requests can consume the entire per-node token bucket at the Gateway and cause the Gateway to reject/throttle every other legitimate subscriber's requests relayed via that same node.

### Finding Description
`HandleNodeMessage` dispatches `MethodWebAPITarget`/`MethodComputeAction`/`MethodWorkflowSyncer` messages received from a DON node to `handleWebAPIOutgoingMessage`, passing only `nodeAddr` (the sending node's identity) [1](#0-0) . Inside `handleWebAPIOutgoingMessage`, the only quota check performed before dispatching the outbound HTTP request is `h.nodeRateLimiter.Allow(nodeAddr)` [2](#0-1) . The `nodeRateLimiter` is a single `ratelimit.RateLimiter` instance constructed once per handler from `HandlerConfig.NodeRateLimiter` and keyed only by whatever string is passed to `Allow` [3](#0-2) [4](#0-3) . Because the only key ever passed is `nodeAddr`, the limiter's "per-sender" bucket is effectively a per-node bucket, shared by every workflow/user whose fetch/compute requests happen to be routed through that node.

Default production configuration for this handler sets `GlobalBurst: 10, GlobalRPS: 50, PerSenderBurst: 10, PerSenderRPS: 10` [5](#0-4) . On the node side, `OutgoingConnectorHandler.handleSingleNodeRequest` throttles outgoing requests per-workflow (`req.WorkflowID`) with defaults `PerSenderRPS: 5, PerSenderBurst: 50` [6](#0-5) [7](#0-6) . A single workflow is thus allowed to burst up to 50 requests almost instantaneously to the Gateway — far exceeding the Gateway's own node-keyed burst of 10 — immediately draining `h.nodeRateLimiter`'s bucket for that `nodeAddr`. Any subsequent `web_api_target`/`compute_action` message forwarded by that same node (belonging to any other user/workflow scheduled on the same node) will then hit `!h.nodeRateLimiter.Allow(nodeAddr)` and be rejected with `"rate limit exceeded for node %s"`, even though those other requests never exceeded their own individual quotas.

No per-workflow, per-sender, or per-subscription check exists at this Gateway layer to prevent this cross-tenant interference; the equivalent v2 gateway handler (`gatewayHandler.HandleNodeMessage` in `http_handler.go`) exhibits the identical node-only keying pattern (`perNodeRateLimiters[nodeAddr]` and `globalNodeRateLimiter`), confirming this is a structural design gap rather than an isolated bug [8](#0-7) .

### Impact Explanation
This is a Denial-of-Service / griefing issue: an unprivileged user who can register and trigger a workflow that executes `web_api_target` or `compute_action` capability calls can exhaust the shared per-node HTTP-fetch quota at the Gateway, causing legitimate `web_api_target`/`compute_action` requests from other subscribers' workflows — that are unrelated to the attacker and never exceeded their own limits — to be throttled/dropped whenever their workflow happens to be scheduled through the same DON node. This maps to a node/gateway-level denial-of-service against other users' workflow executions, not an authentication/authorization bypass or fund-movement issue.

### Likelihood Explanation
No special privilege is required beyond the ability to register and execute a workflow that invokes the `web_api_target`/`compute_action` capability, which is available to any DON tenant/subscriber. Given the mismatch between per-workflow burst allowance on the node side (default burst 50) and the Gateway's per-node burst allowance (default burst 10), a single workflow execution can trivially and repeatably exhaust the shared bucket, especially since node assignment is not attacker-controlled but is a shared resource among many tenants by design.

### Recommendation
Key the Gateway-side outgoing rate limiter (and equivalent v2 `perNodeRateLimiters`) by the actual originating workflow ID / sender identity carried in the message body (`msg.Body.Sender` / workflow ID), in addition to or instead of `nodeAddr`, so that one tenant's outgoing traffic volume cannot deplete quota belonging to other tenants sharing the same DON node. Alternatively, raise the per-node burst to be a function of the number of active tenants/workflows on that node, or add a secondary per-sender rate limiter at this layer that mirrors the node-side per-workflow limiter.

### Proof of Concept
1. In `core/services/gateway/handlers/capabilities/handler_test.go`, construct a `handler` via `NewHandler` with `NodeRateLimiter{GlobalRPS:50, GlobalBurst:10, PerSenderRPS:10, PerSenderBurst:10}` (matching production defaults), mocking `don.SendToNode` and `httpClient.Send`.
2. Simulate "attacker" traffic: call `handler.HandleNodeMessage(ctx, resp, "node1")` 10+ times in quick succession with `MethodWebAPITarget` payloads whose `Body.Sender`/workflow identifier is attacker-controlled, all through `nodeAddr = "node1"`.
3. Assert the first ~10 calls succeed (`err == nil`) and the 11th+ calls return `"rate limit exceeded for node node1"`.
4. Immediately after, dispatch one more `HandleNodeMessage(ctx, resp, "node1")` message whose payload/sender corresponds to a distinct, legitimate, non-attacker workflow.
5. Assert this legitimate message is also rejected with the same `"rate limit exceeded for node node1"` error, demonstrating that `h.nodeRateLimiter.Allow("node1")` denies unrelated subscribers' traffic solely because it shares the same `nodeAddr`, confirming the lack of per-user/per-workflow isolation.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L96-99)
```go
	nodeRateLimiter, err := ratelimit.NewRateLimiter(cfg.NodeRateLimiter)
	if err != nil {
		return nil, err
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

**File:** deployment/cre/jobs/pkg/gateway_job.go (L327-340)
```go
func newDefaultWebAPICapabilitiesHandler() handler {
	return handler{
		Name: GatewayHandlerTypeWebAPICapabilities,
		Config: webAPICapabilitiesHandlerConfig{
			MaxAllowedMessageAgeSec: 1_000,
			NodeRateLimiter: nodeRateLimiterConfig{
				GlobalBurst:    10,
				GlobalRPS:      50,
				PerSenderBurst: 10,
				PerSenderRPS:   10,
			},
		},
	}
}
```

**File:** core/capabilities/webapi/outgoing_connector_handler.go (L110-118)
```go
func (c *OutgoingConnectorHandler) handleSingleNodeRequest(ctx context.Context, messageID string, req capabilities.Request) (*api.Message, error) {
	lggr := logger.With(c.lggr, "messageID", messageID, "workflowID", req.WorkflowID)
	workflowAllow, globalAllow := c.outgoingRateLimiter.AllowVerbose(req.WorkflowID)
	if !workflowAllow {
		return nil, errors.New(errorOutgoingRatelimitWorkflow)
	}
	if !globalAllow {
		return nil, errors.New(errorOutgoingRatelimitGlobal)
	}
```

**File:** core/capabilities/webapi/outgoing_connector_handler.go (L412-426)
```go
func outgoingRateLimiterConfigDefaults(config ratelimit.RateLimiterConfig) ratelimit.RateLimiterConfig {
	if config.GlobalBurst == 0 {
		config.GlobalBurst = DefaultGlobalBurst
	}
	if config.GlobalRPS == 0 {
		config.GlobalRPS = DefaultGlobalRPS
	}
	if config.PerSenderBurst == 0 {
		config.PerSenderBurst = DefaultWorkflowBurst
	}
	if config.PerSenderRPS == 0 {
		config.PerSenderRPS = DefaultWorkflowRPS
	}
	return config
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L238-254)
```go
func (h *gatewayHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	if resp.ID == "" {
		return fmt.Errorf("received response with empty request ID from node %s", nodeAddr)
	}
	h.lggr.Debugw("handling incoming node message", "requestID", resp.ID, "nodeAddr", nodeAddr)
	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementCapabilityNodeThrottled(ctx, nodeAddr, h.lggr)
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementGlobalThrottled(ctx, h.lggr)
		return errors.New("global rate limit exceeded")
	}
```
