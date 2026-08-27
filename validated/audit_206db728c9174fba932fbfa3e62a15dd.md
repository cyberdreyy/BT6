### Title
Missing Freshness/Deadline Validation on Web API Trigger Requests Allows Replay of Signed Trigger Messages - ([File: core/capabilities/webapi/trigger/trigger.go])

### Summary
The `web-api-trigger@1.0.0` capability's `TriggerRequestPayload` carries a `Timestamp` field explicitly documented as needing to be "within certain freshness to be processed," but `triggerConnectorHandler.HandleGatewayMessage`/`processTrigger` in `core/capabilities/webapi/trigger/trigger.go` never validates it. This mirrors the reported bug class: a value meant to bound the validity window of an authorized action (the `deadline` parameter in the DeFi report) is either hard-coded or simply not checked, letting a captured request be replayed/executed long after it was originally authorized.

### Finding Description
The schema for the trigger payload states the field's purpose explicitly: [1](#0-0) 

The generated Go struct carries the same field and comment: [2](#0-1) 

However, `triggerConnectorHandler.HandleGatewayMessage` only validates the message signature/shape via `hc.ValidatedMessageFromReq`, unmarshals the payload, and dispatches straight to `processTrigger` — there is no comparison of `payload.Timestamp` against `time.Now()` or any configured max-age anywhere in this file: [3](#0-2) 

`processTrigger` itself only checks sender allowlisting, per-sender rate limiting, and topic matching before dispatching the event to the registered workflow channel — again, no freshness or replay check: [4](#0-3) 

This is in stark contrast to the sibling/legacy gateway handler for the same concept, `HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`, which explicitly enforces both a non-zero timestamp and a max message age (`MaxAllowedMessageAgeSec`) before accepting a trigger request: [5](#0-4) 

That staleness check is exercised and enforced by tests in the legacy path: [6](#0-5) 

No equivalent check (and no `TriggerEventId` dedup/replay cache) exists on the `core/capabilities/webapi/trigger` code path, even though its payload schema documents the same freshness requirement.

### Impact Explanation
`HandleGatewayMessage` is reachable from the internet-facing Gateway, which relays externally-submitted, signed HTTP-trigger requests to the workflow node. Because the `Timestamp` field is never validated for staleness, and there is no replay/dedup protection on `TriggerEventId`, a previously valid signed trigger message (e.g., one intercepted, logged, or simply retained) can be resubmitted through the gateway at any point in the future and will still pass all currently enforced checks (signature validity, sender allowlist, topic match, rate limit). This results in unauthorized/duplicate execution of a workflow trigger outside of its intended validity window — an "unauthorized job run" analogous to the underlying report's concern about missing deadline checks permitting delayed/held execution of an authorized action.

### Likelihood Explanation
Any unprivileged external client that can observe or capture one valid, signed trigger message directed at a workflow (the signature itself doesn't bind the message to a time window enforced by this handler) can replay it. Because rate limiting is per-sender and topic-based rather than freshness-based, and there's no execution-ID/nonce cache in this file, a stale but validly-signed request is processed exactly like a fresh one.

### Recommendation
Add the same staleness check used in `HandleLegacyUserMessage` to `triggerConnectorHandler.HandleGatewayMessage`/`processTrigger`: reject payloads where `payload.Timestamp` is zero or older than a configured `MaxAllowedMessageAgeSec`, and consider adding a short-lived dedup cache keyed by `body.Sender + payload.TriggerEventId` to guard against replay within the freshness window.

### Proof of Concept
1. A legitimate external client signs and sends a valid `TriggerRequestPayload` (with `TriggerId`, `TriggerEventId`, `Timestamp`, `Topics`, `Params`) through the Gateway to a workflow using the `web-api-trigger@1.0.0` capability; it is accepted and executes the workflow.
2. An attacker who obtains a copy of this exact signed JSON-RPC request (e.g., via network capture, logs, or a compromised intermediary) resubmits the identical payload to the Gateway hours/days later.
3. `HandleGatewayMessage` calls `hc.ValidatedMessageFromReq` (signature still verifies since nothing about the signature depends on current time) and then `processTrigger`, which only checks sender allowlist/topic/rate-limit — all of which still pass — causing the workflow trigger to fire again despite the stale `Timestamp`. [7](#0-6)

### Citations

**File:** core/capabilities/webapi/webapicap/event_trigger-schema.json (L64-68)
```json
                "timestamp": {
                    "type": "integer",
                    "format": "int64",
                    "description": "Timestamp of the event (unix time), needs to be within certain freshness to be processed."
                },
```

**File:** core/capabilities/webapi/webapicap/event_trigger_generated.go (L101-117)
```go
type TriggerRequestPayload struct {
	// Key-value pairs for the workflow engine, untranslated.
	Params TriggerRequestPayloadParams `json:"params" yaml:"params" mapstructure:"params"`

	// Timestamp of the event (unix time), needs to be within certain freshness to be
	// processed.
	Timestamp int64 `json:"timestamp" yaml:"timestamp" mapstructure:"timestamp"`

	// Topics corresponds to the JSON schema field "topics".
	Topics []string `json:"topics" yaml:"topics" mapstructure:"topics"`

	// Uniquely identifies generated event (scoped to trigger_id and sender).
	TriggerEventId string `json:"trigger_event_id" yaml:"trigger_event_id" mapstructure:"trigger_event_id"`

	// ID of the trigger corresponding to the capability ID.
	TriggerId string `json:"trigger_id" yaml:"trigger_id" mapstructure:"trigger_id"`
}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L79-149)
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
	if matchedWorkflows == 0 {
		return errors.New("no Matching Workflow Topics")
	}

	if fullyMatchedWorkflows > 0 {
		return nil
	}
	return err
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L286-302)
```go
	t.Run("sad case stale message", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		}, r)
	})
```
