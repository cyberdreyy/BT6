## Analysis

The WatchPug free-trial finding is fundamentally a **"cancel-and-recreate resets a stateful counter/quota"** bug: destroying and recreating the same logical entity resets a benefit that should persist across the entity's lifetime for a given identity.

The closest reachable analog in this codebase is the **WebAPI trigger's per-workflow rate limiter**, which is fully re-instantiated every time `RegisterTrigger` is called, and can be freely torn down via `UnregisterTrigger` by the same caller.

### Root cause

`triggerConnectorHandler.RegisterTrigger` creates a brand-new `ratelimit.RateLimiter` (with fresh token buckets) each time a trigger is registered for a `TriggerID`: [1](#0-0) 

`UnregisterTrigger` allows the same trigger to be deregistered on demand (closing the channel and deleting the map entry): [2](#0-1) 

The rate limiter itself has no persistent, cross-registration memory — it's an in-memory `map[string]*rate.Limiter` keyed by `perSender`, scoped to the lifetime of the `webapiTrigger` struct: [3](#0-2) 

Enforcement happens in `processTrigger`, which calls `trigger.rateLimiter.Allow(body.Sender)` against whatever rate-limiter instance is currently attached to that `TriggerID`: [4](#0-3) 

### Title
Workflow owners can reset the WebAPI trigger's per-sender/global rate limiter by unregister/register cycling - ([File: core/capabilities/webapi/trigger/trigger.go])

### Summary
The web-api trigger capability (`web-api-trigger@1.0.0`) enforces request throttling via a `ratelimit.RateLimiter` that is newly constructed inside `RegisterTrigger` and discarded on `UnregisterTrigger`. Because the limiter's token-bucket state lives only in memory tied to the current registration and is never persisted or keyed by anything outside the trigger's own lifecycle, a workflow owner can bypass their configured rate limit indefinitely simply by unregistering and re-registering the same trigger (e.g., by redeploying/reactivating the workflow spec), each cycle producing a fully-refilled bucket — directly analogous to the free-trial refund/repurchase loop in the referenced report.

### Finding Description
`RegisterTrigger` builds a fresh `ratelimit.RateLimiter` from the trigger's own submitted `RateLimiter` config every time it is invoked for a `TriggerID` [5](#0-4) . This rate limiter starts with a fully-topped-off `GlobalBurst`/`PerSenderBurst` token bucket [6](#0-5) .

`UnregisterTrigger` is a cheap, self-service operation for the trigger's own workflow: it just closes the channel and deletes the map entry, with no cooldown, no persisted counter, and no check on how recently the trigger was created [2](#0-1) .

Because workflow (re)deployment / pause-resume naturally drives `UnregisterTrigger` followed by `RegisterTrigger` for the same `TriggerID`, an unprivileged workflow owner who controls their own workflow spec can repeatedly cycle registration to obtain an unlimited number of freshly-refilled rate-limit windows, defeating the very purpose of `PerSenderRPS`/`PerSenderBurst`/`GlobalRPS`/`GlobalBurst` throttling that is meant to protect the gateway/node from being flooded with trigger events.

### Impact Explanation
The rate limiter on this trigger exists specifically to bound how fast/frequently a given sender can push web-api trigger events into the node, protecting shared node/gateway resources (goroutines, channel buffers, downstream event processing) from being overwhelmed by a single workflow. By resetting the limiter on each register/unregister cycle, a workflow owner can sustain a burst rate far beyond what was intended, causing resource exhaustion / degraded service for other workflows sharing the same DON/gateway node — a quota-bypass leading to availability impact, similar in class (though not identical in severity) to the referenced finding's "fund loss to owners providing free trials."

### Likelihood Explanation
Any workflow author capable of registering a `web-api-trigger@1.0.0` capability can perform this; no special privilege is required beyond normal workflow ownership. Redeploying/reactivating a workflow (which naturally triggers unregister+register) is an ordinary, cheap, self-service action, so the likelihood of accidental or deliberate abuse is non-trivial, though it does require the attacker to already be an onboarded workflow author with a registered web-api trigger.

### Recommendation
Persist rate-limit state independently of the trigger's registration lifecycle — e.g., key the limiter by a stable identity (workflow owner address / DON-scoped identifier) in a store that survives `UnregisterTrigger`/`RegisterTrigger` cycles, or add a minimum cooldown/backoff before a freshly re-registered trigger for the same owner regains full burst capacity. Consider tracking last-registration time per owner and decaying, rather than resetting, the token bucket.

### Proof of Concept
1. Workflow owner registers a `web-api-trigger` with `PerSenderRPS`/`PerSenderBurst` = N via `RegisterTrigger`.
2. Owner sends N events rapidly, exhausting the token bucket (`Allow` starts returning `false` — see rate limiter semantics at [7](#0-6) ).
3. Owner calls `UnregisterTrigger` for the same `TriggerID` (e.g. by pausing/redeploying the workflow), which is allowed unconditionally [2](#0-1) .
4. Owner immediately calls `RegisterTrigger` again with the same config, which constructs a brand-new `ratelimit.RateLimiter` with full burst capacity [5](#0-4) .
5. Repeat steps 2–4 indefinitely to sustain a request rate arbitrarily higher than the configured `PerSenderRPS`/`GlobalRPS`.

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L96-109)
```go
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

**File:** core/capabilities/webapi/trigger/trigger.go (L196-253)
```go
func (h *triggerConnectorHandler) RegisterTrigger(ctx context.Context, req capabilities.TriggerRegistrationRequest) (<-chan capabilities.TriggerResponse, error) {
	cfg := req.Config
	if cfg == nil {
		return nil, errors.New("config is required to register a web api trigger")
	}

	reqConfig, err := h.ValidateConfig(cfg)
	if err != nil {
		return nil, err
	}

	if len(reqConfig.AllowedSenders) == 0 {
		return nil, errors.New("allowedSenders must have at least 1 entry")
	}

	h.mu.Lock()
	defer h.mu.Unlock()
	_, errBool := h.registeredWorkflows[req.TriggerID]
	if errBool {
		return nil, fmt.Errorf("triggerId %s already registered", req.TriggerID)
	}

	rateLimiterConfig := reqConfig.RateLimiter
	commonRateLimiter := ratelimit.RateLimiterConfig{
		GlobalRPS:      rateLimiterConfig.GlobalRPS,
		GlobalBurst:    int(rateLimiterConfig.GlobalBurst),
		PerSenderRPS:   rateLimiterConfig.PerSenderRPS,
		PerSenderBurst: int(rateLimiterConfig.PerSenderBurst),
	}

	rateLimiter, err := ratelimit.NewRateLimiter(commonRateLimiter)
	if err != nil {
		return nil, err
	}

	allowedSendersMap := map[string]bool{}
	for _, k := range reqConfig.AllowedSenders {
		allowedSendersMap[k] = true
	}

	allowedTopicsMap := map[string]bool{}
	for _, k := range reqConfig.AllowedTopics {
		allowedTopicsMap[k] = true
	}

	ch := make(chan capabilities.TriggerResponse, defaultSendChannelBufferSize)

	h.registeredWorkflows[req.TriggerID] = webapiTrigger{
		workflowID:     req.Metadata.WorkflowID,
		allowedTopics:  allowedTopicsMap,
		allowedSenders: allowedSendersMap,
		ch:             ch,
		config:         *reqConfig,
		rateLimiter:    rateLimiter,
	}

	return ch, nil
}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L255-266)
```go
func (h *triggerConnectorHandler) UnregisterTrigger(ctx context.Context, req capabilities.TriggerRegistrationRequest) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	workflow, ok := h.registeredWorkflows[req.TriggerID]
	if !ok {
		return fmt.Errorf("triggerId %s not registered", req.TriggerID)
	}

	close(workflow.ch)
	delete(h.registeredWorkflows, req.TriggerID)
	return nil
}
```

**File:** core/services/workflows/ratelimiter/ratelimiter.go (L10-52)
```go
// Wrapper around Go's rate.Limiter that supports both global and a per-sender rate limiting.
type RateLimiter struct {
	global    *rate.Limiter
	perSender map[string]*rate.Limiter
	config    Config
	mu        sync.Mutex
}

type Config struct {
	GlobalRPS      float64 `json:"globalRPS"`
	GlobalBurst    int     `json:"globalBurst"`
	PerSenderRPS   float64 `json:"perSenderRPS"`
	PerSenderBurst int     `json:"perSenderBurst"`
}

func NewRateLimiter(cfg Config) (*RateLimiter, error) {
	if cfg.GlobalRPS <= 0.0 || cfg.PerSenderRPS <= 0.0 {
		return nil, errors.New("RPS values must be positive")
	}
	if cfg.GlobalBurst <= 0 || cfg.PerSenderBurst <= 0 {
		return nil, errors.New("burst values must be positive")
	}

	return &RateLimiter{
		global:    rate.NewLimiter(rate.Limit(cfg.GlobalRPS), cfg.GlobalBurst),
		perSender: make(map[string]*rate.Limiter),
		config:    cfg,
	}, nil
}

func (rl *RateLimiter) Allow(sender string) (senderAllow bool, globalAllow bool) {
	rl.mu.Lock()
	senderLimiter, ok := rl.perSender[sender]
	if !ok {
		senderLimiter = rate.NewLimiter(rate.Limit(rl.config.PerSenderRPS), rl.config.PerSenderBurst)
		rl.perSender[sender] = senderLimiter
	}
	rl.mu.Unlock()

	senderAllow = senderLimiter.Allow()
	globalAllow = rl.global.Allow()
	return senderAllow, globalAllow
}
```
