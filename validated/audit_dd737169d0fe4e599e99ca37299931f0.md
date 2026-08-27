### Title
Legacy gateway messages reach `HandleLegacyUserMessage` and broadcast to a victim DON's nodes without any sender-to-DON authorization check - ([File: core/services/gateway/gateway.go])

### Summary
`gateway.ProcessRequest` selects the legacy handler purely by matching `msg.Body.DonId` against the configured `g.handlers` map after `msg.Validate()`, which only validates message structure/signature format, not that the signer/sender is an authorized member of that DON. The downstream `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` broadcasts the attacker-controlled message to every node in `h.donConfig.Members` with an explicit `// TODO: apply allowlist and rate-limiting here` comment confirming no allowlist check exists at that layer either.

### Finding Description
In `gateway.ProcessRequest` (`core/services/gateway/gateway.go:250-262`), when `msg.Body.DonId != ""`, the code treats the request as legacy, calls `msg.Validate()`, and looks up the handler by `handlerKey = msg.Body.DonId`: [1](#0-0) 
It then dispatches to `h.HandleLegacyUserMessage(ctx, msg, callback)`: [2](#0-1) 

Neither `ProcessRequest` nor `msg.Validate()` checks that the message's sender/signer is a member of the DON identified by `msg.Body.DonId` - it only routes to whichever handler object is registered for that DON ID. This is not a "handler confusion" bug (the correct DON's handler is selected), but the handler itself performs no sender allowlist check before broadcasting: `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go:341-421` validates payload structure, timestamp freshness, and method name, but explicitly has a `// TODO: apply allowlist and rate-limiting here` before checking the method, and then unconditionally forwards the request to all `h.donConfig.Members`: [3](#0-2) 

Because there is no verification that the requester is a subscribed/allowlisted sender for that specific DON, any client capable of producing a message that passes `msg.Validate()` (structural/signature-format validation, not DON-membership validation) can force the gateway to relay a `web_api_trigger` request to every node of an arbitrary DON it names via `msg.Body.DonId`, consuming that DON's capability resources.

### Impact Explanation
This corresponds to a resource-consumption / unauthorized DON access impact: an attacker who is not a subscribed member of a target DON can cause the gateway to broadcast attacker-chosen webapi-trigger payloads to that DON's nodes, forcing them to process/execute capability requests they did not authorize. This does not directly leak secrets or move funds, but it violates the "only subscribed senders may address a DON" authorization invariant and can be used for resource exhaustion / unsolicited job triggering against a DON.

### Likelihood Explanation
The precondition is low: the attacker only needs to construct a message that passes `msg.Validate()`'s structural checks (self-generated signing key is sufficient, per the question's framing) and knows/guesses a target `DonId` (DON IDs are generally not secret - they appear in gateway configuration and are often discoverable). No node/operator/admin access is required. This is repeatable per request and is not blocked by any existing allowlist check, as confirmed by the explicit TODO in the handler code.

### Recommendation
Add sender-authorization checks before dispatching legacy messages:
1. In `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go`), verify the message signer is present in `h.donConfig.Members` (or an equivalent authorized-sender allowlist for that DON) before saving the callback and broadcasting to `don.SendToNode`.
2. Alternatively/additionally, perform this check earlier in `gateway.ProcessRequest` right after `msg.Validate()` succeeds for legacy requests, rejecting messages whose recovered signer is not authorized for `msg.Body.DonId`.
3. Implement the rate-limiting mentioned in the existing TODO to bound resource consumption from repeated unauthorized legacy requests.

### Proof of Concept
Go handler-level integration test (extends existing `handler_test.go` patterns and `gateway_test.go`'s `newGatewayWithMockHandler`):
1. Set up a `gateway` with `donConfig.Members` containing only nodes for DON `"donA"`, using the real `capabilities.handler` (not a mock) wired to a mock `handlers.DON` that records `SendToNode` calls.
2. Craft an `api.Message` with `Body.DonId = "donA"`, `Body.Method = MethodWebAPITrigger`, a valid `TriggerRequestPayload` with fresh timestamp, and a signature generated from an attacker-owned key not associated with any node in `donA`'s member list.
3. Call `gateway.ProcessRequest` with the JSON-RPC encoded message.
4. Assert that `HandleLegacyUserMessage` still forwards the message via `don.SendToNode` to all of `donA`'s members (expected/current behavior showing the vulnerability), i.e., no error such as `"sender not authorized for DON"` is returned and `SendToNode` is invoked once per member.
5. To validate the fix, re-run after adding an allowlist check and assert `ProcessRequest` returns `api.HandlerError` (or similar) and `SendToNode` is never called when the signer/sender is not in `donConfig.Members`.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-419)
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
```
