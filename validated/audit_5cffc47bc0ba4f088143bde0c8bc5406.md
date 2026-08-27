### Title
Gateway `HandleLegacyUserMessage` Forwards Unvalidated Requests to All DON Nodes Without Sender Allowlist or Rate-Limit Enforcement - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The `CreditCaller` report describes a case where the contract's stated intent ("only listed collateral tokens are accepted") is not actually enforced in code, letting unprivileged users supply arbitrary, unvalidated input that gets processed anyway. The Chainlink gateway has an analogous pattern in `handler.HandleLegacyUserMessage`: the code contains an explicit acknowledgment that allowlist and rate-limiting *should* be applied, but the implementation never applies them before forwarding the caller-supplied request to every node in the DON.

### Finding Description
`handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` processes an inbound message from an external (unprivileged) client. It validates the payload's JSON shape, timestamp staleness, and method name, but the sender/topic allowlist check that exists on the newer trigger path is missing here — the code literally contains the comment: [1](#0-0) 
right before it only checks `msg.Body.Method != MethodWebAPITrigger` and then broadcasts the validated request to **every DON member** unconditionally: [2](#0-1) 

By contrast, the newer/parallel trigger-registration path (`triggerConnectorHandler.processTrigger` in `core/capabilities/webapi/trigger/trigger.go`) explicitly requires `AllowedSenders` to be non-empty at registration time and rejects any sender not present in the per-workflow allowlist, plus applies a per-sender rate limiter, before dispatching the trigger event: [3](#0-2) [4](#0-3) 

This confirms that allowlist/rate-limit enforcement is the intended and expected security control at this trust boundary (an unprivileged, internet-facing client submitting requests through the gateway) — exactly the same "specification says X, code doesn't enforce X" pattern as the `CreditCaller` collateral check.

### Impact Explanation
In the legacy path, any external caller able to reach the gateway's HTTP-message handling can submit a `MethodWebAPITrigger` message that is saved as a callback and fanned out to all DON node members, without any sender allowlist or DON-side rate-limiting check gating this fan-out. This mirrors the "arbitrary/unlisted input accepted where a specification implies filtering" bug class: unauthorized senders can cause the gateway to dispatch requests to every node, bypassing the workflow-owner/sender scoping and quota protections that the rest of the codebase (the modern trigger path) treats as mandatory.

### Likelihood Explanation
The `HandleLegacyUserMessage` function is directly exposed to unprivileged/external callers per its own naming and doc comment ("processes incoming messages from the Gateway" originating from user-facing HTTP requests), and the missing-check is explicitly flagged by the developers' own `TODO` comment, indicating this is a known, currently-unaddressed gap rather than a hypothetical one.

### Recommendation
Apply the same sender allowlist and per-sender/global rate-limiting checks used in `triggerConnectorHandler.processTrigger` (or an equivalent DON-configured allowlist) inside `HandleLegacyUserMessage` before saving the callback and broadcasting to `don.SendToNode` for all members, and remove the `TODO` once the check is implemented and tested.

### Proof of Concept
Not independently reproducible from static analysis alone — reachability depends on whether the legacy HTTP message endpoint that invokes `HandleLegacyUserMessage` is still routed/enabled in the deployed gateway configuration. Test coverage in `core/services/gateway/handlers/capabilities/handler_test.go` explicitly notes this gap: [5](#0-4) 
confirming sender/rate-limit validation for this path is untested and unresolved, consistent with the missing enforcement identified above.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-385)
```go
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
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

**File:** core/capabilities/webapi/trigger/trigger.go (L196-209)
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
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```
