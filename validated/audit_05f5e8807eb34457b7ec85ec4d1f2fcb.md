## Finding

The unbounded storage-array iteration pattern from the MultiFeeDistribution audit report has a close structural analog in `chainlink`'s WebAPI Trigger gateway handler.

### Title
Unbounded iteration over `registeredWorkflows` map on every incoming gateway trigger message enables per-request DoS scaling with workflow count - (File: `core/capabilities/webapi/trigger/trigger.go`)

### Summary
`triggerConnectorHandler.processTrigger` is invoked once per incoming gateway message from an external, unprivileged caller (via `HandleGatewayMessage`). It performs a full O(N×M) scan of **every** registered workflow trigger (N = `len(h.registeredWorkflows)`) against **every** topic in the request (M = `len(payload.Topics)`), with no cap on either dimension, mirroring the audited pattern of iterating an entire unbounded storage collection in response to a single user action.

### Finding Description
`HandleGatewayMessage` [1](#0-0)  decodes an untrusted payload arriving from the Gateway (ultimately originating from an external HTTP/WebAPI caller) and, for `MethodWebAPITrigger`, calls `processTrigger` synchronously.

`processTrigger` iterates the entire `h.registeredWorkflows` map for every single incoming request, and for each registered workflow iterates all attacker-supplied `payload.Topics`: [2](#0-1) 

Neither dimension is bounded:
- `registeredWorkflows` grows without limit as more workflows register the `web-api-trigger@1.0.0` capability via `RegisterTrigger` [3](#0-2) , with no maximum-size eviction analogous to the `MaxSavedCallbacks`/`pruneCallbacks` bound used elsewhere in the gateway capabilities handler [4](#0-3) .
- `payload.Topics` is fully attacker-controlled and unbounded in length; the code only rejects the empty case [5](#0-4) .

This is directly analogous to the reported Solidity bug class: a single external actor's request triggers a full walk of an ever-growing storage/state collection (`userEarnings`/`userLocks` there, `registeredWorkflows` here), with per-request cost scaling linearly (or worse) with total accumulated state rather than with the caller's own state.

Additionally, unlike `RegisterTrigger`/`UnregisterTrigger`, which correctly hold `h.mu` while mutating `registeredWorkflows`, `processTrigger` reads `h.registeredWorkflows` **without** acquiring `h.mu` at all, meaning this is also a concurrent-map-access hazard when other goroutines add/remove triggers — but the core DoS concern is the unbounded work per message.

### Impact Explanation
As the number of workflows/nodes registering the `web-api-trigger` capability grows on a given node/DON, the per-message processing cost of `processTrigger` grows proportionally. A caller can additionally pad `payload.Topics` with many entries to multiply the inner-loop cost. Because `HandleGatewayMessage` runs this synchronously per gateway message, a burst of trigger messages (or messages with many topics) can degrade or stall trigger processing for the whole node/DON, delaying legitimate workflow executions — a service-availability degradation reachable from an unprivileged external caller, consistent with the "medium" classification given to the original unbounded-iteration finding.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a reasonably large number of registered workflow triggers to accumulate over time (plausible in a busy DON, since there is no eviction/cap), and/or (b) an attacker submitting requests with a large `Topics` array, which is not size-limited in the payload decoding path. No special privileges beyond being an allowed sender for at least one topic (to pass rate-limiting checks and avoid early `err` returns) — and even unauthorized senders still cause the full map/topic scan to execute before the sender/rate-limit checks reject them.

### Recommendation
- Index registered workflows by topic (e.g., `map[topic][]webapiTrigger`) instead of scanning the full map per request, so cost scales with matched topics rather than total registered workflows.
- Bound `payload.Topics` length and reject oversized requests early, before iterating registered triggers.
- Acquire `h.mu` (or use a read lock / sync.Map) while reading `registeredWorkflows` in `processTrigger` to avoid concurrent map access with `RegisterTrigger`/`UnregisterTrigger`.
- Consider an upper bound / LRU-style eviction on `registeredWorkflows`, similar to the `MaxSavedCallbacks` pattern already used in the sibling `handlers/capabilities` package.

### Proof of Concept
1. Register many workflows (N) via `RegisterTrigger`, each with distinct `allowedTopics`.
2. As an external caller (any sender, even one not in `allowedSenders` for most triggers), submit a gateway `web_api_trigger` message with a large `Topics` array (M entries).
3. `processTrigger` performs N×M iterations synchronously per message; repeated/concurrent submissions from a single unprivileged caller degrade trigger-processing throughput for the whole handler, since there is no bound on N (registered workflows) or M (topics per request) and no per-request cost limit prior to the scan.

### Citations

**File:** core/capabilities/webapi/trigger/trigger.go (L88-91)
```go
	// empty topics is error for V1
	if len(topics) == 0 {
		return errors.New("empty Workflow Topics")
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

	// If there are still too many callbacks, sort them by creation time and remove the oldest ones.
	maxSize := h.config.MaxSavedCallbacks
	var evicted int
	if len(h.savedCallbacks) > maxSize {
		type entry struct {
			id        string
			createdAt time.Time
		}
		entries := make([]entry, 0, len(h.savedCallbacks))
		for id, cb := range h.savedCallbacks {
			entries = append(entries, entry{id, cb.createdAt})
		}
		sort.Slice(entries, func(i, j int) bool {
			return entries[i].createdAt.Before(entries[j].createdAt)
		})
		// Trim to maxSize/2 to avoid sorting the list too frequently.
		for _, e := range entries[:len(entries)-maxSize/2] {
			delete(h.savedCallbacks, e.id)
			evicted++
		}
	}

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
	}
}
```
