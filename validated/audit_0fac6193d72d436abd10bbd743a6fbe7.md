### Title
Cross-tenant WebAPI trigger response hijacking via unauthenticated MessageId collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The gateway's `savedCallbacks` map is keyed solely by the client-supplied `MessageId`, which is never bound to the requester's signer/address. Any unprivileged signer can submit a second `web_api_trigger` request reusing a victim's still-pending `MessageId`, silently overwriting the victim's registered callback, so that the eventual DON node response addressed to that `MessageId` is delivered to the attacker instead of the original requester.

### Finding Description
`HandleLegacyUserMessage` stores a callback keyed only by `msg.Body.MessageId` with no check for an existing, unresolved entry and no binding to the caller's identity: [1](#0-0) 

The `MessageId` is fully attacker-controlled: it comes straight from the JSON-RPC request `ID` field or `Message.Body.MessageId`, set by `ValidatedMessageFromReq`/gateway's `ProcessRequest` without any linkage to `msg.Body.Sender` (which is derived later, purely from the signature over the message body, and is never checked against `MessageId` uniqueness): [2](#0-1) [3](#0-2) 

The code even documents that authorization on this path is absent — `// TODO: apply allowlist and rate-limiting here` — right before the callback is stored: [4](#0-3) 

On the response path, `handleWebAPITriggerMessage` looks up and deletes the callback purely by `MessageId`, delivering the response to whatever `Callback` currently occupies that slot, without verifying it corresponds to the original requester's session: [5](#0-4) 

Exploit flow:
1. Victim sends a signed `web_api_trigger` request with `MessageId = "X"`. Gateway calls `HandleLegacyUserMessage`, which stores `savedCallbacks["X"] = victimCallback` and forwards the request to DON nodes.
2. Before the DON responds, attacker (any signer — no allowlist/authorization check exists at this layer) sends their own signed `web_api_trigger` request also using `MessageId = "X"`. `HandleLegacyUserMessage` unconditionally overwrites `savedCallbacks["X"] = attackerCallback`.
3. When a DON node later responds with `MessageId = "X"` (the response for the victim's original request, since node-to-gateway routing is keyed by the same ID), `HandleNodeMessage` → `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds `attackerCallback` (the victim's entry was overwritten), and delivers the victim's response payload to the attacker via `savedCb.SendResponse(...)`.
4. The victim's original HTTP request either times out or never receives a response, while the attacker receives data/response content addressed to the victim.

`HandleNodeMessage`'s only authentication is `msg.Body.Sender != nodeAddr` — this validates that the *node* forwarding the response is legitimate, but does nothing to bind the response back to the correct originating user session, since that binding is entirely delegated to the unauthenticated `MessageId` key.

### Impact Explanation
This breaks the gateway's request-binding invariant that a request/response pair must be attributable to exactly one authenticated sender. An attacker can intercept another tenant's in-flight `web_api_trigger` response, which may contain sensitive workflow trigger data (e.g., proprietary price feed payloads, business data pushed through the `web_api_trigger` capability). This is a cross-user response confusion / information disclosure issue matching the "request impersonation" / "unauthorized access to another user's data" bounty impact class.

### Likelihood Explanation
Exploitation requires only the ability to send a signed gateway JSON-RPC message with an attacker-chosen `MessageId` matching (or racing) a victim's pending request ID — no membership in a workflow allowlist, no special role, and no node/operator access. The `TODO: apply allowlist and rate-limiting` comment confirms no sender-based restriction currently exists on this path. The main practical constraint is winning a timing race against the DON's response latency (typically hundreds of ms to seconds), and either predicting/observing a victim's `MessageId` (many client implementations use static or low-entropy/sequential IDs, as seen in `core/scripts/gateway/web_api_trigger/invoke_trigger.go` which defaults to `"12345"`) or brute-forcing short IDs since `MessageIdMaxLen` allows arbitrarily short strings. This is repeatable per request.

### Recommendation
Bind `savedCallbacks` entries to the requester's identity (e.g., key by `(MessageId, Sender)` or store the expected `Sender`/`Receiver` alongside the callback and verify it matches before overwriting or delivering), and reject `HandleLegacyUserMessage` calls that attempt to reuse a `MessageId` already present in `savedCallbacks` (return a "duplicate message ID" error) instead of silently overwriting the existing entry.

### Proof of Concept
Go table test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build `victimMsg` signed by `victimKey` with `MessageId = "collide-1"` and a valid `TriggerRequestPayload`. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`; assert `handler.savedCallbacks["collide-1"]` is bound to `victimCb`.
2. Build `attackerMsg` signed by a different `attackerKey`, same `MessageId = "collide-1"`, valid payload. Call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)`; assert `handler.savedCallbacks["collide-1"]` now points to `attackerCb` (overwritten).
3. Simulate a legitimate DON node response addressed to `MessageId = "collide-1"` (as would be generated for the victim's original forwarded request) via `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
4. Assert `attackerCb.Wait(ctx)` returns the response payload (i.e., attacker receives the victim's data), and `victimCb.Wait(ctx)` times out / never receives a response — proving cross-tenant response hijacking.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/message_util.go (L36-58)
```go
func ValidatedMessageFromReq(req *jsonrpc.Request[json.RawMessage]) (*api.Message, error) {
	if req.Version != "2.0" {
		return nil, errors.New("incorrect jsonrpc version")
	}
	if req.Method == "" {
		return nil, errors.New("empty method field")
	}
	if req.Params == nil {
		return nil, errors.New("missing params attribute")
	}
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
}
```

**File:** core/services/gateway/gateway.go (L264-277)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

```
