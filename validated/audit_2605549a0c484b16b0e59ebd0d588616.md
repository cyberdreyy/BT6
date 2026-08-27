### Title
Cross-user response hijacking via colliding `MessageId` in legacy WebAPI trigger handler — no requester binding on `savedCallbacks` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores a user's `Callback` in `h.savedCallbacks` keyed solely by the attacker-controlled `msg.Body.MessageId`, with no uniqueness check and no binding to the sender/requester identity. `handleWebAPITriggerMessage` (invoked from `HandleNodeMessage` on `MethodWebAPITrigger`) subsequently looks up and deletes the callback purely by `msg.Body.MessageId`, so whichever caller most recently registered that ID receives the DON's response.

### Finding Description
`HandleLegacyUserMessage` unconditionally overwrites the map entry with no conflict check: [1](#0-0) 

This is unlike the newer v2 implementation, `setupCallback`, which explicitly rejects an in-flight duplicate `requestID`: [2](#0-1) 

The node's later response is matched back to a caller purely by `MessageId`, deleting whatever entry is currently stored and delivering the response to it, with no verification that the responder's request originated from the same sender/session: [3](#0-2) 

`MessageId` is fully attacker-controlled: it is taken directly from the JSON-RPC request `ID` field / signed `api.Message.Body.MessageId` when converting an incoming message to a legacy request, with no server-side uniqueness enforcement: [4](#0-3) 

Exploit flow: the victim submits a `web_api_trigger` request with `MessageId = X`, which is stored in `savedCallbacks[X]`. Before the DON node's response arrives, the attacker submits their own signed request also using `MessageId = X`. `HandleLegacyUserMessage` overwrites `savedCallbacks[X]` with the attacker's callback (no conflict check exists in this legacy path). When the DON node's reply for `MessageId = X` arrives — whether it is a reply to the victim's original request or a reply to the attacker's colliding request, since the DON echoes back `body.MessageId` from whatever request it received — `handleWebAPITriggerMessage` looks it up in `savedCallbacks[X]`, finds the attacker's callback (last writer wins), deletes the entry, and delivers the response body to the attacker instead of the victim. This is a race but is repeatable since the attacker can retry the collision on every observed/guessed `MessageId` and the round-trip window (gateway → DON → gateway) is nontrivial.

The existing checks (`payload.Timestamp` staleness check, `MethodWebAPITrigger` check, `ValidatedRequestFromMessage`) all validate message structure/signature validity but do nothing to bind the callback to a caller identity or reject a colliding `MessageId`, so none of them stop this.

### Impact Explanation
This is a cross-user response confusion / disclosure vulnerability: an unprivileged attacker who can predict or observe another user's `MessageId` can intercept the DON's trigger response payload intended for that user, exfiltrating potentially sensitive workflow trigger response data. This matches Chainlink's "cross-user response confusion" bounty impact class.

### Likelihood Explanation
Requires only the ability to send a signed `api.Message`/gateway request (any external client with a valid key able to reach the gateway's user-message endpoint for this DON, no elevated role needed), and knowledge/prediction of the victim's `MessageId`, plus timing to submit the colliding request before the DON responds. Since there is no uniqueness/conflict check in this legacy handler path (unlike the v2 handler which explicitly guards against it), the race is only bounded by the round-trip latency between the gateway and DON, which is generally on the order of at least tens/hundreds of milliseconds — feasible to win, and repeatable across many attempted `MessageId`s.

### Recommendation
In `HandleLegacyUserMessage`, reject (or return a conflict response for) requests whose `MessageId` already exists in `h.savedCallbacks`, mirroring the `setupCallback` conflict check in the v2 `httpTriggerHandler`. Additionally, bind the saved callback to the requester's identity (e.g., session/sender/connection) and verify that identity matches when delivering the response in `handleWebAPITriggerMessage`, and prefer generating/validating `MessageId` uniqueness server-side rather than trusting client-supplied IDs.

### Proof of Concept
Go handler-level integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a valid `triggerRequest` message with `MessageId = "collide-1"` from "victim" key, call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`.
2. Before simulating the node's response, construct a second valid `triggerRequest` message with the same `MessageId = "collide-1"` from an "attacker" key, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)`.
3. Assert `handler.savedCallbacks["collide-1"]` now points to the attacker's callback (via reflection/pointer identity or observable behavior).
4. Simulate the DON node's reply for `MessageId = "collide-1"` via `handler.HandleNodeMessage(ctx, nodeResp, nodeAddr)` (or directly call `handleWebAPITriggerMessage`).
5. Assert `attackerCb.Wait(ctx)` returns the response payload (proving interception) and `victimCb.Wait(ctx)` times out / never receives a response — demonstrating cross-user response disclosure.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L410-414)
```go

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
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
