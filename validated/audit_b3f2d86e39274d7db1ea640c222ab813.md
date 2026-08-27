# Title
Unbounded per-message trigger-matching cost combined with unbounded blocking channel send freezes the Gateway connector's message loop - (File: `core/capabilities/webapi/trigger/trigger.go`)

### Summary
`processTrigger` in the Web API trigger connector handler iterates over the *entire* map of registered workflow triggers for every single incoming gateway message, and then blocks indefinitely (bounded only by the connection's lifetime context, not by a per-message timeout) trying to deliver a matched event to a trigger's channel. Because this handler is invoked synchronously inside the connector's single-threaded per-gateway `readLoop`, both the unbounded iteration cost and the unbounded blocking send can stall processing of all subsequent messages from that gateway — the same class of issue as the reported Gravity Bridge finding, where an unbounded-size data structure (there, the validator set; here, the registered-triggers map / a full channel) makes a single required operation arbitrarily expensive or blocking, freezing the pipeline that depends on it.

### Finding Description
Every JSON-RPC message read from a Gateway connection is dispatched synchronously, one at a time, inside `gatewayConnector.readLoop`: [1](#0-0) 

For the web-api-trigger capability, the dispatched handler is `triggerConnectorHandler.HandleGatewayMessage`, which for `MethodWebAPITrigger` calls `processTrigger`: [2](#0-1) 

`processTrigger` performs an O(N×M) scan over **all** currently registered workflow triggers (`h.registeredWorkflows`, a map with no size limit) crossed with the topics supplied in the (attacker/client-controlled) trigger payload, for every incoming message: [3](#0-2) 

There is no cap on how large `registeredWorkflows` can grow — `RegisterTrigger` inserts new entries unconditionally after only validating the request's own config: [4](#0-3) 

In addition, on a match, the code blocks on a channel send with only the connector's long-lived shutdown context as a bound — there is no per-message/per-send timeout: [5](#0-4) 

If the destination trigger's buffered channel (`defaultSendChannelBufferSize = 1000`) fills up because the workflow engine consuming it is slow, stalled, or simply not draining it fast enough, this `select` can block for the entire remaining lifetime of the gateway connection. Since `HandleGatewayMessage` is invoked synchronously from `readLoop`, this single stuck (or merely large-and-slow) trigger message prevents `readLoop` from ever reading the next message off `gatewayState.conn.ReadChannel()` — i.e., it freezes processing of *every* subsequent message from that Gateway, exactly analogous to the Gravity bug where a single required, unbounded-cost operation (`makeCheckpoint` over an oversized validator set) can exceed available resources and brick the pipeline until manual intervention.

### Impact Explanation
An unprivileged/external actor who can trigger enough `web_api_trigger` requests to cause matches (or simply cause a slow consumer on the other end of a registered trigger's channel) can degrade or fully stall the gateway connector's message loop for a given DON/gateway connection, blocking delivery of all other gateway traffic (including unrelated capability responses) until the connection is torn down and re-established by the reconnect loop. As the number of registered triggers naturally grows over the life of a node (more workflows subscribing to web API triggers), the fixed per-message cost of `processTrigger` grows without bound, worsening latency/availability for all trigger consumers — a systemic availability (DoS) issue rather than a data-confidentiality or fund-movement issue.

### Likelihood Explanation
Likelihood is moderate: it requires either (a) accumulation of a large number of registered triggers over time (natural operational growth, not attacker-controlled directly) or (b) a slow/stalled consumer on a registered trigger's channel while new matching messages keep arriving — both plausible in production without any special privilege, since the trigger channel send has no timeout distinct from the whole-connection shutdown context.

### Recommendation
- Bound the cost of `processTrigger` independent of the total number of registered triggers, e.g., index triggers by topic (`map[topic][]webapiTrigger`) instead of scanning the full map for every message.
- Add a short, per-message timeout (not tied to the connector's shutdown context) around the `trigger.ch <- tr` send so a slow/stalled consumer cannot block the shared `readLoop`.
- Consider dispatching `HandleGatewayMessage` (or at least the trigger delivery) off the `readLoop` goroutine (e.g., via a bounded worker pool) so a single slow handler cannot stall ingestion of subsequent Gateway messages.
- Add an explicit maximum on `registeredWorkflows` size or alert when it grows large, similar to bounding validator-set growth in the referenced report.

### Proof of Concept
1. Register enough `web_api_trigger` workflows (or one workflow whose consuming engine is intentionally slow/paused without unregistering) so that its channel buffer of 1000 fills up.
2. Send a `web_api_trigger` message via the Gateway matching that trigger's topic/sender.
3. Observe that `processTrigger`'s `select { case trigger.ch <- tr: }` blocks because the channel is full and `ctx` here is the connector's long-lived shutdown context, not a short-lived per-request one.
4. Because `HandleGatewayMessage` is called synchronously from `gatewayConnector.readLoop`, no further messages from that Gateway (for any handler) are read or processed until the block resolves or the connection is reset — demonstrating the freeze condition analogous to the Gravity Bridge report.

### Citations

**File:** core/services/gateway/connector/connector.go (L273-297)
```go
	for {
		select {
		case <-c.shutdownCh:
			return
		case item := <-gatewayState.conn.ReadChannel():
			var req jsonrpc.Request[json.RawMessage]
			err := json.Unmarshal(item.Data, &req)
			if err != nil {
				c.lggr.Errorw("parse error when reading from Gateway", "id", gatewayState.config.ID, "err", err)
				break
			}
			c.handlersMu.RLock()
			handler, exists := c.handlers[req.Method]
			c.handlersMu.RUnlock()
			if !exists {
				c.lggr.Errorw("no handler for method", "id", gatewayState.config.ID, "method", req.Method)
				break
			}
			// do not break on error. HandleGatewayMessage handles errors
			// by sending a response back to the Gateway.
			err = handler.HandleGatewayMessage(ctx, gatewayState.config.ID, &req)
			if err != nil {
				c.lggr.Warnw("failed to handle message from Gateway", "id", gatewayState.config.ID, "method", req.Method, "err", err)
			}
		}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L93-140)
```go
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
				fullyMatchedWorkflows++
				TriggerEventID := body.Sender + payload.TriggerEventId

				// Emit trigger execution started event
				workflowExecutionID, genErr := events.GenerateExecutionID(trigger.workflowID, TriggerEventID)
				if genErr != nil {
					h.lggr.Errorw("failed to generate execution ID", "err", genErr)
					workflowExecutionID = ""
				}
				emitErr := events.EmitTriggerExecutionStarted(ctx, map[string]string{}, TriggerEventID, workflowExecutionID)
				if emitErr != nil {
					h.lggr.Errorw("failed to emit trigger execution started event", "err", emitErr)
				}

				tr := capabilities.TriggerResponse{
					Event: capabilities.TriggerEvent{
						TriggerType: TriggerType,
						ID:          TriggerEventID,
						Outputs:     wrappedPayload,
					},
				}
				select {
				case <-ctx.Done():
					return nil
				case trigger.ch <- tr:
					// Sending n topics that match a workflow with n allowedTopics, can only be triggered once.
					break
				}
			}
		}
	}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L170-185)
```go
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
