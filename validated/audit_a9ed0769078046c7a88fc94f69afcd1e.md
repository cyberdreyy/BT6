## Title
Unbounded per-message topic loop in `webapiTrigger.processTrigger` allows unprivileged gateway senders to cause CPU-bound slowdowns and stall subsequent gateway messages - (File: core/capabilities/webapi/trigger/trigger.go)

### Summary
`triggerConnectorHandler.processTrigger` iterates over every registered workflow trigger and, for each one, over the full `payload.Topics` slice supplied by the caller, with no upper bound on the number of topics accepted from an unprivileged request body. [1](#0-0)  This mirrors the "slow ABCI method" bug class reported against Allora's `SafeApplyFuncOnAllActiveEpochEndingTopics`: a handler whose per-call cost scales with the product of an internally-growing dataset (registered workflows) and an externally-controlled dataset (attacker-supplied topics), invoked synchronously on every incoming request.

### Finding Description
`HandleGatewayMessage` decodes an incoming JSON-RPC request from the gateway, unmarshals the attacker-controlled `body.Payload` into `webapicap.TriggerRequestPayload`, and for the `MethodWebAPITrigger` method calls `processTrigger` synchronously before responding. [2](#0-1) 

`processTrigger` then runs a nested loop: for every entry in `h.registeredWorkflows` (all workflows currently subscribed to this trigger capability on the node) it iterates the full `topics := payload.Topics` array taken directly from the unauthenticated/unprivileged caller's payload, with no cap on `len(topics)`. [3](#0-2)  No length validation of `payload.Topics` exists anywhere in this handler or in `RegisterTrigger`, and I could not find any gateway-level request/body size limit specific to this path that would meaningfully cap the number of topics (searches for `MaxRequestBodyBytes`/size limits in `core/services/gateway/**` only surfaced generic HTTP/WS server config, not a per-field bound on decoded JSON arrays).

The test suite for the gateway connector explicitly documents that `HandleGatewayMessage` is invoked from a single goroutine per connection and the connector blocks on it: "The gateway connector calls HandleGatewayMessage from a single goroutine per connection and waits for it to return... serving inline forced requests through one at a time and the tail of a burst outlived the caller's timeout." [4](#0-3)  The webapi trigger connector runs the same per-connection HandleGatewayMessage model, so a single slow `processTrigger` call directly delays processing of every other queued message on that connection.

Additionally, `processTrigger` reads `h.registeredWorkflows` without holding `h.mu`, while `RegisterTrigger`/`UnregisterTrigger` mutate that same map under `h.mu.Lock()`. [5](#0-4) [6](#0-5)  This compounds the DoS concern because a long-running unsynchronized iteration overlapping with a concurrent registration/unregistration can also race on the map.

### Impact Explanation
As the number of workflows registered to the `web-api-trigger` capability grows, each unprivileged trigger request costs `O(registeredWorkflows × len(payload.Topics))`. Because `payload.Topics` is entirely attacker-controlled and unbounded, a single malicious or malformed request can force a large computation on the node, blocking the connector's per-connection message loop and delaying/timing out other legitimate gateway traffic to that node — directly analogous to the reported "slow ABCI method" impact (node/service slowdown, potential processing halt for that connection) but scoped to the webapi-trigger gateway path rather than consensus.

### Likelihood Explanation
Reachable from any unprivileged actor able to send a `MethodWebAPITrigger` message through the gateway to a node running this capability — no special role or authentication beyond being an allowed sender/topic is required to reach `processTrigger`'s cost-driving loop, since the cost is paid before the per-workflow `allowedSenders`/`allowedTopics` checks filter matches. Likelihood increases with the number of workflows subscribed via this capability, which is expected to grow with adoption.

### Recommendation
- Enforce a strict upper bound on `len(payload.Topics)` in `processTrigger` (or in payload validation before it), rejecting oversized requests early with an error response.
- Avoid the full O(workflows × topics) scan by indexing registered triggers by topic (e.g., `map[topic][]webapiTrigger`) so lookups are proportional to `len(topics)` and matching triggers only.
- Take `h.mu.RLock()`/`RUnlock()` around the read of `h.registeredWorkflows` in `processTrigger` to eliminate the data race with `RegisterTrigger`/`UnregisterTrigger`.
- Consider processing/validating the request off the connector's blocking read path (e.g., via a bounded worker pool) so a single expensive request cannot stall other messages on the same connection.

### Proof of Concept
1. Register N workflows to the `web-api-trigger` capability (e.g., N = 10,000), each with distinct `allowedTopics`.
2. As an unprivileged external actor, send a single gateway message with `Method: MethodWebAPITrigger` and a payload containing `Topics` with a very large array (e.g., 100,000 entries) that do not match any `allowedTopics`.
3. Observe `processTrigger` performing N × 100,000 map lookups synchronously inside `HandleGatewayMessage`, which per the connector's documented single-goroutine-per-connection design blocks processing of all other queued messages on that connection until this call returns. [7](#0-6) [4](#0-3)

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L79-100)
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
```

**File:** core/capabilities/webapi/trigger/trigger.go (L151-184)
```go
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

**File:** core/capabilities/webapi/trigger/trigger.go (L211-216)
```go
	h.mu.Lock()
	defer h.mu.Unlock()
	_, errBool := h.registeredWorkflows[req.TriggerID]
	if errBool {
		return nil, fmt.Errorf("triggerId %s already registered", req.TriggerID)
	}
```

**File:** core/capabilities/confidentialrelay/handler_test.go (L1026-1031)
```go
// TestHandler_ServesRequestsConcurrently is the regression guard for the
// head-of-line block that made a burst of relay requests fail. The gateway
// connector calls HandleGatewayMessage from a single goroutine per connection
// and waits for it to return, so serving inline forced requests through one at a
// time and the tail of a burst outlived the caller's timeout. Each call must
// return promptly and the requests must overlap.
```
