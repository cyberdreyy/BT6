### Title
Legacy `web_api_trigger` messages bypass sender allowlist/subscription checks and are forwarded to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`Handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` only validates payload decoding, message staleness, and method name before forwarding the request to every node in the target DON via `don.SendToNode`. There is no per-sender allowlist or subscription check anywhere on this path, and the code explicitly contains a `// TODO: apply allowlist and rate-limiting here` comment confirming the gap.

### Finding Description
The request path is: `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes the raw JSON-RPC body into an `api.Message`, and when `msg.Body.DonId != ""` it treats the request as legacy. It only checks:
1. `msg.Validate()` (`core/services/gateway/api/message.go:54-88`) — validates signature length/format, field lengths, and recovers `Body.Sender` from the ECDSA signature (any attacker holding a private key can self-sign, this proves nothing about entitlement).
2. `g.handlers[msg.Body.DonId]` existence — i.e., whether the DonId string is a DON *known to the gateway at all*, not whether the sender is a member of that DON's allowlist/subscription.

If both checks pass, `h.HandleLegacyUserMessage(ctx, msg, callback)` is called [1](#0-0) . Inside the capabilities handler, `HandleLegacyUserMessage` decodes the trigger payload, checks staleness and that `Method == MethodWebAPITrigger`, and then unconditionally forwards the signed request to every member of `h.donConfig.Members` via `don.SendToNode` [2](#0-1) . No allowlist, subscription, or per-sender entitlement check for that specific DON is performed — the comment `// TODO: apply allowlist and rate-limiting here` at line 384 directly confirms this is a known, unimplemented gap rather than intentional design.

Consequently, any unprivileged party that can produce a validly-signed `api.Message` (using any ECDSA keypair — no registration required) with `DonId` set to any DON the gateway operator has configured can have that message routed and its `web_api_trigger` payload dispatched to all nodes of that DON, regardless of whether the sender has any subscription or allowlist relationship with that DON/workflow owner.

### Impact Explanation
This is an allowlist/entitlement bypass that lets an attacker cause the DON's nodes to receive and act on an unauthorized `web_api_trigger` message (e.g., triggering workflow executions) for a DON/service they are not entitled to interact with. This matches the "allowlist/quota bypass" and "unauthorized job run" impact classes called out in the task description.

### Likelihood Explanation
Exploitability requires only: (1) knowledge/discovery of a valid `DonId` string configured on the target gateway (these are often not secret — they can appear in public gateway configs, docs, or be brute-forced/enumerated since there is no rate limit on invalid guesses beyond generic HTTP throttling), and (2) the ability to sign an arbitrary message with any ECDSA key, which is trivial and requires no credentials from the gateway operator. No allowlist entry, subscription, or prior relationship with the DON is needed. This is fully repeatable and scriptable.

### Recommendation
Implement the allowlist/subscription check called out by the existing `// TODO` comment: before forwarding in `HandleLegacyUserMessage`, verify `msg.Body.Sender` against the DON's configured allowlist/subscription for the resolved `donConfig`/method (mirroring whatever allowlist mechanism exists for the JSON-RPC/new-format path) and reject with an authorization error if the sender is not permitted, prior to calling `don.SendToNode`.

### Proof of Concept
Handler-level integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a `donConfig` that has a defined `Members` list and no allowlist configured for a fresh, unregistered signer.
2. Generate a fresh ECDSA key not present in any allowlist/subscription store for the DON.
3. Build a legacy `api.Message` with `Body.Method = MethodWebAPITrigger`, `Body.DonId` = the DON's ID, a valid `TriggerRequestPayload` with current `Timestamp`, and sign it with the fresh key via `msg.Sign`.
4. Call `h.HandleLegacyUserMessage(ctx, msg, callback)` with a mock `handlers.DON`.
5. Assert current (vulnerable) behavior: `don.SendToNode` (mock) is invoked once per `donConfig.Members` entry — proving the message was forwarded despite the sender having no allowlist/subscription entry.
6. Expected behavior after fix: `HandleLegacyUserMessage` should return/callback an authorization error (e.g., `api.UnauthorizedError`) and `don.SendToNode` should never be called for the unregistered signer.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
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
	return err
```
