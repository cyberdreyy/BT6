### Title
Missing sender allowlist check in `handler.HandleLegacyUserMessage` allows any signed message to be fanned out to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` validates message freshness and structural correctness but performs no sender-allowlist or rate-limiting check before dispatching a `MethodWebAPITrigger` message to every node in the DON via `don.SendToNode`. The code explicitly flags this gap with `// TODO: apply allowlist and rate-limiting here`, meaning any holder of a valid ECDSA key — not just senders registered via `RegisterTrigger`'s `AllowedSenders` — can force the gateway to broadcast their message to all DON members.

### Finding Description
The request enters via `gateway.ProcessRequest` in [1](#0-0) , which for legacy requests only calls `msg.Validate()` (structural/signature validation) before routing to `h.HandleLegacyUserMessage(ctx, msg, callback)`. There is no per-sender authorization check at this layer.

Inside `HandleLegacyUserMessage`, the checks performed are: payload decodability, non-zero timestamp, and message freshness (`MaxAllowedMessageAgeSec`). Immediately after these checks, the code contains the explicit marker: [2](#0-1) 
and then, without any allowlist or rate-limit check, transforms the message and fans it out to every DON member: [3](#0-2) 

Any attacker who signs an `api.Message` with `Method = MethodWebAPITrigger` using a freshly generated, never-registered ECDSA key can pass all checks in `HandleLegacyUserMessage` (valid structure, non-zero fresh timestamp) and get the gateway to call `don.SendToNode` for every node configured in `h.donConfig.Members`, regardless of whether that sender appears in any capability's `AllowedSenders` list.

The actual per-workflow authorization only happens later, downstream, inside each DON node's `triggerConnectorHandler.processTrigger` in `core/capabilities/webapi/trigger/trigger.go`, which checks `trigger.allowedSenders[sender.String()]` per registered trigger topic before emitting a `TriggerResponse`: [4](#0-3) 

So while unauthorized senders cannot ultimately trigger a workflow execution (the `allowedSenders` check in `trigger.go` blocks that), the gateway ingress layer performs no defense-in-depth rejection — it unconditionally forwards every well-formed, freshly-timestamped, validly-signed message to all DON nodes, consuming DON node processing/network capacity and the gateway's own per-message state (`savedCallbacks` map entry) for arbitrary unregistered senders.

### Impact Explanation
This matches the "resource exhaustion / bypass of intended early rejection" impact class: any unprivileged party with a fresh key can force fan-out of arbitrary signed `web_api_trigger` payloads to every node of a DON, consuming node message-processing and network resources, and creates inconsistent enforcement between the gateway ingress layer (no allowlist) and the trigger capability layer (has allowlist), which is a defense-in-depth gap explicitly acknowledged by the `TODO` comment in the code. It does not, by itself, allow unauthorized workflow execution, because `trigger.go`'s `allowedSenders` check still gates that outcome.

### Likelihood Explanation
The precondition is trivial: the attacker needs no registration, no allowlist membership, and no special role — just any ECDSA keypair to sign an `api.Message`, matching an "unauthenticated/unprivileged sender" threat model. The request is fully reproducible and repeatable since the gateway performs no session state or prior registration check before accepting `MethodWebAPITrigger` messages.

### Recommendation
Implement the allowlist/rate-limiting check called out in the `TODO` at [5](#0-4)  before dispatching to `don.SendToNode` in `HandleLegacyUserMessage` — e.g., verify `msg.Body.Sender` against a gateway-level allowlist/rate limiter (similar to `h.nodeRateLimiter` used for outgoing messages) prior to the fan-out loop at lines 417-419, so unauthorized senders are rejected at ingress rather than only at the downstream trigger capability.

### Proof of Concept
1. In a test based on `core/services/gateway/handlers/capabilities/handler_test.go`, construct a `HandlerConfig` and `DONConfig` with several `Members`, and a mock `handlers.DON` implementing `SendToNode`.
2. Sign an `api.Message` with `Body.Method = MethodWebAPITrigger`, a valid fresh `Timestamp`, and a well-formed `TriggerRequestPayload`, using a freshly generated ECDSA key that is not present in any `AllowedSenders` list.
3. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)` directly.
4. Assert (current/expected-to-fail-safe behavior): `don.SendToNode` mock is invoked once per DON member (demonstrating current unconditional fan-out) — the fix should instead assert `SendToNode` is never called and `callback.SendResponse` is invoked with an authorization/allowlist error code.

### Citations

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
