### Title
Missing sender/DON allowlist enforcement in `HandleLegacyUserMessage` allows unauthorized capability trigger execution - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` validates payload structure, timestamp freshness, and method name, but performs no check that the message sender is a subscribed/allowlisted workflow owner before broadcasting the request to every DON member via `don.SendToNode`. The `// TODO: apply allowlist and rate-limiting here` comment sits directly above the method-name check, confirming the authorization step was never implemented in this code path.

### Finding Description
The flow is: `gateway.ProcessRequest` decodes and validates message envelope/signature via `msg.Validate()` [1](#0-0) , then dispatches legacy requests to `h.HandleLegacyUserMessage(ctx, msg, callback)` [2](#0-1) . Inside `HandleLegacyUserMessage`, the handler checks payload decoding, `Timestamp != 0`, and message staleness, then hits the TODO comment followed immediately by only a method-equality check (`msg.Body.Method != MethodWebAPITrigger`) — no lookup against any per-workflow/DON subscription or allowlist structure exists in this handler (`h.config`, `h.don`, `h.donConfig` contain no allowlist/authorizer field) [3](#0-2) . After that, `common.ValidatedRequestFromMessage` only re-derives a JSON-RPC request from the already-signature-validated message (proving *who* signed, not *whether they're authorized*), and the handler proceeds to fan the request out to every DON member: `don.SendToNode(ctx, member.Address, req)` for all `h.donConfig.Members` [4](#0-3) . Signature validation (`msg.Validate()`) proves message integrity/sender identity via ECDSA recovery, but this only demonstrates the message wasn't tampered with — it does not restrict *which* keys are permitted to trigger the DON. Any address capable of generating a valid ECDSA signature over a `Message{Method: MethodWebAPITrigger, Timestamp: <fresh>, DonId: <target>}` passes every check in this function.

This gap is self-acknowledged in the codebase: the corresponding test file explicitly notes `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` at the end of the trigger test suite [5](#0-4) , and none of the "sad case" tests in that suite exercise an unauthorized-sender scenario [6](#0-5) .

Notably, the newer v2 HTTP capability handler (`core/services/gateway/handlers/capabilities/v2/http_handler.go`) does implement a `metadataHandler` described as "Handles authorization for HTTP trigger requests" plus per-workflow rate limiting via `userRateLimiter` before invoking `triggerHandler.HandleUserTriggerRequest` [7](#0-6) [8](#0-7) , confirming that authorization enforcement is an established pattern in this codebase that is simply absent from the legacy `v1` handler under review, and that its absence is a known, intentional gap flagged for future work rather than an incidental oversight.

### Impact Explanation
An attacker holding any ECDSA key pair (no subscription, no allowlist entry, no prior relationship with the DON) can craft and self-sign a `MethodWebAPITrigger` message targeting an arbitrary `DonId` and have the gateway broadcast it to every member node of that DON via `don.SendToNode`. This triggers webapi-trigger capability execution/resource consumption on all DON nodes under an arbitrary, non-subscribed identity — this matches "unauthorized job/capability execution" / allowlist bypass impact class, causing DON node resource consumption and potential downstream workflow-trigger side effects attributable to a spoofed/unauthorized owner.

### Likelihood Explanation
Preconditions are minimal: attacker needs only an ECDSA keypair (freely generatable) and knowledge of a valid `DonId` (these are generally discoverable/public gateway configuration). No credentials, subscription, or prior authorization are required. The message only needs a fresh `Timestamp` and correct `Method`/`DonId` fields — trivially satisfied. This is fully repeatable and scriptable against any exposed gateway endpoint using this legacy `v1` webapi-trigger handler.

### Recommendation
Implement the allowlist/authorization check called out by the TODO before forwarding to `don.SendToNode`: validate `msg.Body.Sender` (recovered from the verified signature) against a per-DON/per-workflow subscription or allowlist (similar to the `metadataHandler`/authorizer pattern used in the v2 HTTP capability handler), and reject unauthorized senders with `api.UnauthorizedError` (or equivalent) before any broadcast occurs. Additionally add per-sender rate-limiting consistent with the TODO comment.

### Proof of Concept
Go handler-level test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct `handler` with a `donConfig` containing legitimate DON members and a mock `handlers.DON`.
2. Generate a fresh, unrelated ECDSA key (`attackerKey`) with no entry in any allowlist/subscription structure known to the handler.
3. Build a `Message` with `Method: MethodWebAPITrigger`, `Timestamp: time.Now().Unix()`, `DonId: <handler's donConfig.DonId>`, sign it with `attackerKey`, and set `msg.Body.Sender` accordingly (matching current test helper `triggerRequest`).
4. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)`.
5. Assert (current behavior, demonstrating the bug): `err == nil`, `don.SendToNode` (mock) was called once per DON member with the attacker's forwarded request, and no allowlist/authorization error is returned via `callback.Wait`.
6. This proves the absence of sender authorization — the fix should make this same test assert a rejection (e.g., `api.UnauthorizedError`) instead.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
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
```

**File:** core/services/gateway/gateway.go (L267-269)
```go
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-396)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L397-420)
```go
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L265-363)
```go
	})

	t.Run("sad case invalid method", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)

		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method foo",
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		}, r)
	})

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

	t.Run("sad case empty payload", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "{}")
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				"error decoding payload field params in TriggerRequestPayload: required",
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		}, r)
	})

	t.Run("sad case invalid payload", func(t *testing.T) {
		invalidMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidMsg, cb)
		require.NoError(t, err)
		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				invalidMsg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				"error decoding payload field params in TriggerRequestPayload: required",
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		}, r)
	})
	t.Run("savedCallbacks stored only when message is valid", func(t *testing.T) {
		require.Empty(t, handler.savedCallbacks)

		invalidPayloadMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidPayloadMsg, cb)
		require.NoError(t, err)
		_, _ = cb.Wait(t.Context())

		staleMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb2 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, staleMsg, cb2)
		require.NoError(t, err)
		_, _ = cb2.Wait(t.Context())

		badMethodMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb3 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, badMethodMsg, cb3)
		require.NoError(t, err)
		_, _ = cb3.Wait(t.Context())

		handler.mu.Lock()
		require.Empty(t, handler.savedCallbacks, "error paths must not leave entries in savedCallbacks")
		handler.mu.Unlock()
	})
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L59-60)
```go
	triggerHandler         HTTPTriggerHandler
	metadataHandler        *WorkflowMetadataHandler // Handles authorization for HTTP trigger requests
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L147-151)
```go

	userRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create user rate limiter: %w", err)
	}
```
