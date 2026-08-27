### Title
Replay of a captured signed gateway `Message` re-dispatches the request to the DON and can silently overwrite a still-pending callback - ([File: core/services/gateway/api/message.go], [File: core/services/gateway/handlers/capabilities/handler.go])

### Finding Description
`(*Message).Validate()` at [1](#0-0)  only checks field lengths/formats and recovers the signer via `ExtractSigner()`; it never checks `MessageId` uniqueness, a nonce, or a timestamp. `gateway.ProcessRequest` calls this `Validate()` for every legacy request before dispatching to the DON-specific handler [2](#0-1) , so a byte-for-byte replay of a previously captured, fully-signed `Message` passes `Validate()` identically to the original.

The only anti-replay control lives downstream in `(*handler).HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go`), which rejects messages whose `payload.Timestamp` is older than `MaxAllowedMessageAgeSec` [3](#0-2) . This bounds the replay window but does not prevent replay *within* that window — a message can be resubmitted verbatim any number of times before it goes stale, and each resubmission passes through to:

```go
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
...
for _, member := range h.donConfig.Members {
    err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
}
``` [4](#0-3) 

This is a plain map assignment keyed only by `MessageId` — it does not check whether an entry already exists, so a replay with the same `MessageId` **silently overwrites** any still-pending callback for that ID rather than being rejected, exactly as hypothesized. If the original request is still in flight (node has not yet responded) when the replay is processed, the map entry now points at the replayed callback. When the DON node's response later arrives, `handleWebAPITriggerMessage` looks up `savedCallbacks[msg.Body.MessageId]`, deletes it, and calls `SendResponse` **once** on whatever entry is currently stored [5](#0-4) . This means the attacker's replayed request receives the response, while the original caller's HTTP request never completes (hangs until its own timeout) — a cross-user response confusion / minor DoS on the legitimate requester.

Separately, each replay does independently cause `don.SendToNode` to be re-invoked for every DON member, re-dispatching the trigger request to the workflow's webapi trigger capability (`processTrigger` in `core/capabilities/webapi/trigger/trigger.go`). However, for workflows run on the V2 engine, execution-level deduplication exists further downstream: `Engine.startExecution` computes a deterministic `executionID` from `workflowID + triggerEventID + triggerIndex` and calls `ExecutionsStore.Add`, which returns `ErrDuplicateExecution` and skips re-execution when the same trigger event is seen twice [6](#0-5) , confirmed by `TestEngine_DeduplicatesSameEventID` [7](#0-6) . Since `TriggerEventID` for the webapi trigger is derived deterministically from `body.Sender + payload.TriggerEventId` [8](#0-7) , a byte-identical replay produces the same `executionID` and is deduplicated for V2-engine consumers, substantially mitigating (but not eliminating at the gateway layer) the "re-run victim's capability execution" impact.

### Impact Explanation
The confirmed, unmitigated impact is at the gateway/handler layer: an attacker who observes one valid, signed `Message` can resubmit it during its staleness window to (a) redundantly re-invoke `don.SendToNode` for all DON members, generating extra load/rate-limiter consumption per resubmission, and (b) race the original in-flight request to have `savedCallbacks[MessageId]` overwritten, causing the response meant for the original caller to be delivered to the attacker's own callback instead (cross-user response confusion) while the original caller's request hangs. Whether this also causes duplicate downstream *workflow execution* (and thus duplicate side effects/quota consumption) depends on whether the specific capability consumer implements execution-level dedup like the V2 engine does; that engine-level protection is not universal and is external to the code under audit (`Validate()`/`HandleLegacyUserMessage`).

### Likelihood Explanation
Preconditions: attacker must have observed one valid signed `Message` (e.g., via logs, a misconfigured proxy, or traffic capture) and must replay it before `MaxAllowedMessageAgeSec` elapses; racing the callback-overwrite scenario additionally requires the original request to still be in flight when the replay arrives, which is a narrow but reproducible timing window (typical DON round-trip latency). No signing key or credentials are required — this is a pure replay, matching the "no key needed" precondition. The map-overwrite is deterministic and always reachable given only `MessageId` correlation, independent of timing, for the "response confusion" case.

### Recommendation
Add a genuine one-time-use / anti-replay check in the gateway path: reject (rather than overwrite) a `HandleLegacyUserMessage` call whose `msg.Body.MessageId` already exists in `savedCallbacks` (mirroring the `RequestCache.NewRequest` "request already exists" pattern), and consider tracking recently-seen `(Sender, MessageId)` pairs across the full `MaxAllowedMessageAgeSec` window (not just in-memory pending callbacks) to reject stale-but-not-yet-expired replays outright.

### Proof of Concept
Go handler-level test plan (`core/services/gateway/handlers/capabilities/handler_test.go`):
1. Build a valid, signed `api.Message` with `Method = MethodWebAPITrigger` and a valid `TriggerRequestPayload{Timestamp: time.Now().Unix()}`.
2. Call `handler.HandleLegacyUserMessage(ctx, msg, callback1)`, assert `don.SendToNode` mock invoked once per DON member, and assert `handler.savedCallbacks[msg.Body.MessageId]` holds `callback1`.
3. Before `callback1` receives a response, call `handler.HandleLegacyUserMessage(ctx, msg, callback2)` with the identical `msg` (same signature/body) — assert no error is returned and `don.SendToNode` is invoked again (duplicate dispatch), and assert `handler.savedCallbacks[msg.Body.MessageId]` now holds `callback2`, silently discarding `callback1`.
4. Simulate the node response via `handler.HandleGatewayMessage`/`handleWebAPITriggerMessage` for that `MessageId` and assert `callback2.Wait()` receives the response while `callback1.Wait()` times out/never resolves — demonstrating cross-user response confusion caused by the unguarded map assignment at `handler.go:412`.

### Citations

**File:** core/services/gateway/api/message.go (L54-88)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
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

**File:** core/services/workflows/v2/engine.go (L773-788)
```go
	// disallow duplicate executions
	_, addErr := e.cfg.ExecutionsStore.Add(ctx, nil, executionID, e.cfg.WorkflowID, store.StatusStarted)
	if addErr != nil {
		if errors.Is(addErr, store.ErrDuplicateExecution) {
			lggr.Infow("Skipping duplicate execution", "executionID", executionID, "triggerID", wrappedTriggerEvent.triggerCapID, "triggerIndex", wrappedTriggerEvent.triggerIndex)
			tm := e.metrics.With(platform.KeyTriggerID, wrappedTriggerEvent.triggerCapID)
			tm.IncrementTriggerExecutionDeduplicatedCounter(ctx)
			tm.IncrementWorkflowTriggerEventErrorCounter(ctx)
			tm.IncrementTriggerEventDroppedTotal(ctx, monitoring.TriggerDropReasonDuplicateExecution)
			registrationID := TriggerRegistrationID(e.cfg.WorkflowID, wrappedTriggerEvent.triggerIndex)
			err = e.ackTriggerEvent(ctx, wrappedTriggerEvent.triggerCapID, registrationID, &triggerEvent)
			if err != nil {
				e.lggr.Errorw("failed to re-ACK trigger event", "eventID", triggerEvent.ID, "err", err)
			}
			return
		}
```

**File:** core/services/workflows/v2/engine_test.go (L1993-2074)
```go
func TestEngine_DeduplicatesSameEventID(t *testing.T) {
	if testing.Short() {
		t.Skip("too slow for testing.Short")
	}

	t.Parallel()

	module := modulemocks.NewModuleV2(t)
	module.EXPECT().Start()
	module.EXPECT().Close()
	capreg := regmocks.NewCapabilitiesRegistry(t)
	capreg.EXPECT().LocalNode(matches.AnyContext).Return(newNode(t), nil)
	billingClient := setupMockBillingClient(t)

	initDoneCh := make(chan error)
	subscribedToTriggersCh := make(chan []string, 1)
	executionFinishedCh := make(chan string, 2)

	cfg := defaultTestConfig(t, nil)
	cfg.Module = module
	cfg.CapRegistry = capreg
	cfg.BillingClient = billingClient
	cfg.Hooks = v2.LifecycleHooks{
		OnInitialized: func(err error) {
			initDoneCh <- err
		},
		OnSubscribedToTriggers: func(triggerIDs []string) {
			subscribedToTriggersCh <- triggerIDs
		},
		OnExecutionFinished: func(executionID string, _ string) {
			executionFinishedCh <- executionID
		},
	}

	engine, err := v2.NewEngine(cfg)
	require.NoError(t, err)

	// Single trigger subscription.
	module.EXPECT().Execute(matches.AnyContext, mock.Anything, mock.Anything).Return(newTriggerSubs(1), nil).Once()

	trigger := capmocks.NewTriggerCapability(t)
	capreg.EXPECT().GetTrigger(matches.AnyContext, "id_0").Return(trigger, nil)
	eventCh := make(chan capabilities.TriggerResponse, 2)
	trigger.EXPECT().RegisterTrigger(matches.AnyContext, mock.Anything).Return(eventCh, nil).Once()
	trigger.EXPECT().UnregisterTrigger(matches.AnyContext, mock.Anything).Return(nil)
	trigger.EXPECT().AckEvent(matches.AnyContext, mock.Anything, mock.Anything, mock.Anything).Return(nil)

	// Only ONE execution should reach Module.Execute.
	module.EXPECT().Execute(matches.AnyContext, mock.Anything, mock.Anything).
		Return(nil, nil).
		Once()

	require.NoError(t, engine.Start(t.Context()))
	require.NoError(t, <-initDoneCh)
	require.Equal(t, []string{"id_0"}, <-subscribedToTriggersCh)

	// Send two events with the same ID through a single trigger channel.
	duplicateEvent := capabilities.TriggerResponse{
		Event: capabilities.TriggerEvent{
			TriggerType: "basic-trigger@1.0.0",
			ID:          "same_event_id",
		},
	}
	eventCh <- duplicateEvent
	eventCh <- duplicateEvent

	wantExecID := wantExecutionID(t, cfg.WorkflowID, "same_event_id", 0)

	select {
	case execID := <-executionFinishedCh:
		require.Equal(t, wantExecID, execID)
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for first execution to finish")
	}

	// No second execution should appear.
	select {
	case execID := <-executionFinishedCh:
		t.Fatalf("unexpected duplicate execution: %s", execID)
	case <-time.After(200 * time.Millisecond):
		// expected
	}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L111-119)
```go
				TriggerEventID := body.Sender + payload.TriggerEventId

				// Emit trigger execution started event
				workflowExecutionID, genErr := events.GenerateExecutionID(trigger.workflowID, TriggerEventID)
				if genErr != nil {
					h.lggr.Errorw("failed to generate execution ID", "err", genErr)
					workflowExecutionID = ""
				}
				emitErr := events.EmitTriggerExecutionStarted(ctx, map[string]string{}, TriggerEventID, workflowExecutionID)
```
