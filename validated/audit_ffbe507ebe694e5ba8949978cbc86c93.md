### Title
HandleLegacyUserMessage forwards `web_api_trigger` from any validly-signed sender without verifying subscription/authorization for the target DON - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` only checks that `msg.Body.Method == MethodWebAPITrigger`, that the message is not stale, and that the payload decodes; it performs no check that the signer of the message is a registered/subscribed user for the target `DonId`/workflow. Any address that can produce a validly-signed `api.Message` can have its `web_api_trigger` request forwarded to every node of any configured DON.

### Finding Description
The gateway's `ProcessRequest` (`core/services/gateway/gateway.go`) routes a legacy request (one with `msg.Body.DonId` set) by calling `msg.Validate()` — which only checks structural validity and ECDSA signature correctness — and then looks up the handler purely by `handlerKey = msg.Body.DonId` [1](#0-0) . It never checks whether the signer of the message is registered/subscribed to that DON or to any workflow running on it.

Inside `HandleLegacyUserMessage`, the code explicitly documents the gap with `// TODO: apply allowlist and rate-limiting here` immediately before the only authorization-adjacent check, which merely verifies `msg.Body.Method != MethodWebAPITrigger` [2](#0-1) . After that, the function unconditionally builds a node-bound request and broadcasts it to every member of `h.donConfig.Members` [3](#0-2) . There is no allowlist, subscription table, or per-sender/per-DON authorization lookup anywhere in this function or in the call path leading to it.

The existing unit tests in `handler_test.go` confirm this gap is known and unaddressed: `TestHandlerReceiveHTTPMessageFromClient` signs messages with arbitrary test node keys and successfully drives the "happy case" through to node dispatch, and the file contains an explicit unresolved TODO: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [4](#0-3) .

### Impact Explanation
An unprivileged attacker with any ECDSA keypair can sign an `api.Message` with `Method: web_api_trigger` and a `DonId` for any DON/workflow they discover (DON IDs are often not secret—they're part of workflow configuration), and have the gateway broadcast the trigger request to every node of that DON, invoking the `web_api_trigger` capability for a DON/workflow the attacker has no registered relationship with. This is an authorization/allowlist bypass: it allows request impersonation/injection of trigger events into workflows belonging to other users, potentially causing unauthorized job/workflow execution. This falls under the "unauthorized job run" / "allowlist bypass" bounty impact class.

Note: whether this results in actual unauthorized *execution* on the DON side depends on downstream validation inside the capability/workflow runtime (e.g., `core/capabilities/webapi/trigger/trigger.go`), which enforces its own allowlist per workflow. I was not able to fully verify within the available context whether that downstream trigger capability rejects unregistered senders before executing — this is the remaining uncertainty. However, the question is scoped specifically to `HandleLegacyUserMessage`, and within that function/handler, no allowlist or subscription check exists, confirming the gateway layer itself performs no authorization filtering for `web_api_trigger` messages beyond signature validity.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only an ECDSA keypair (self-generated, free) and knowledge of a valid `DonId` string for a target DON (these are often discoverable/non-secret configuration values). No credentials, roles, or prior registration with the DON are required. The exploit is fully reproducible via a simple unit/integration test as shown by the existing `triggerRequest` helper in `handler_test.go`, which signs a message with an arbitrary key and DON ID and passes validation and dispatch without any authorization check.

### Recommendation
Implement the allowlist check referenced by the existing TODO comment in `core/services/gateway/handlers/capabilities/handler.go`: before forwarding a `web_api_trigger` (or any) request to DON nodes, verify that `msg.Signature`'s recovered signer address is present in an allowlist/subscription registry scoped to `msg.Body.DonId` (and ideally to the specific workflow/trigger referenced in the payload). Reject with `api.UnauthorizedError` (or equivalent) if the sender is not authorized. This check should be added as a step in `HandleLegacyUserMessage` prior to the `don.SendToNode` loop at lines 417-419.

### Proof of Concept
```go
func TestHandlerReceiveHTTPMessageFromClient_UnregisteredSigner(t *testing.T) {
    handler, _, don, _ := setupHandler(t)
    ctx := t.Context()

    // Generate a brand-new, unregistered EOA key — not part of donConfig.Members
    // and not subscribed to any workflow on this DON.
    randomKey, err := crypto.GenerateKey()
    require.NoError(t, err)

    msg := triggerRequest(t, randomKey, []string{"daily_price_update"}, "", "", "")
    // msg.Body.DonId == "workflow_don_1" (a real, configured DON)

    // Expect broadcast to ALL don members despite signer never being registered/subscribed.
    don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

    cb := hc.NewCallback()
    err = handler.HandleLegacyUserMessage(ctx, msg, cb)
    require.NoError(t, err) // <-- currently succeeds; should be rejected with an authorization error

    don.AssertExpectations(t) // confirms request WAS forwarded to DON nodes
}
```
Expected (fixed) behavior: `HandleLegacyUserMessage` should return/send an `UnauthorizedError` response and `don.SendToNode` should never be called for `randomKey`, since it is not part of any allowlist/subscription for `workflow_don_1`.

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L236-365)
```go
func TestHandlerReceiveHTTPMessageFromClient(t *testing.T) {
	handler, _, don, nodes := setupHandler(t)
	ctx := t.Context()
	msg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "", "")
	codec := api.JsonRPCCodec{}

	t.Run("happy case", func(t *testing.T) {
		// sends to 2 dons
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()

		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, msg, cb)
		require.NoError(t, err)

		resp, err := hc.ValidatedResponseFromMessage(msg)
		require.NoError(t, err)
		err = handler.HandleNodeMessage(ctx, resp, nodes[0].Address)
		require.NoError(t, err)

		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError}, r)
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

	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
