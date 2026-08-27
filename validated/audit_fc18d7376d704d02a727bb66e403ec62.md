### Title
Cross-user response hijacking via colliding client-controlled `MessageId` in the Gateway's legacy Web API handler - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The gateway's legacy `WebAPIHandler` (`core/services/gateway/handlers/capabilities/handler.go`) correlates a user's HTTP request to its eventual DON response using a `savedCallbacks` map that is keyed **only by the client-supplied `MessageId`**, with no uniqueness check and no scoping by sender/session. Because `MessageId` is taken directly from the untrusted JSON-RPC request `ID` field sent by any caller of the gateway's public HTTP endpoint, one unprivileged client can silently overwrite another in-flight caller's saved callback simply by submitting a request with a colliding ID. This causes the original caller's response to be lost and the DON's response to be delivered to the second (attacker-controlled) caller instead — the same "shared, un-scoped resource silently overwritten and captured by another caller" root cause pattern described in the external ICHI report, where funds intended for one user ended up in a shared location that the next caller could claim.

### Finding Description
In `gateway.go`, the JSON-RPC request `ID` supplied by any HTTP client becomes the `MessageId` used throughout the pipeline: [1](#0-0) [2](#0-1) 

For "legacy" DON-routed requests, `gateway.ProcessRequest` calls `h.HandleLegacyUserMessage(ctx, msg, callback)`: [3](#0-2) 

Inside `capabilities.handler.HandleLegacyUserMessage`, the callback for the pending request is stored keyed solely by the client-controlled `msg.Body.MessageId`, with no check for an existing/in-flight entry, and no sender-scoping: [4](#0-3) 

When the DON eventually responds, `handleWebAPITriggerMessage` looks the callback back up purely by `MessageId` and delivers the response to whichever callback is currently stored under that key: [5](#0-4) 

This is structurally different from, and weaker than, other request-correlation mechanisms in the same codebase:
- `common.requestCache.NewRequest` explicitly rejects duplicate keys and scopes the key by `{sender, id}` rather than `id` alone: [6](#0-5) 
- The newer HTTP trigger handler (v2) explicitly detects and rejects duplicate/in-flight request IDs, returning a `Conflict` error to the second caller rather than silently overwriting state, as shown by the "duplicate request ID" test: [7](#0-6) 

The legacy `WebAPIHandler.savedCallbacks` map has neither of these protections, so it inherits none of the collision safety that the rest of the gateway relies on.

### Impact Explanation
If two unrelated callers (e.g., different workflow owners, or a malicious client racing a legitimate one) send legacy Web API trigger requests to the same DON with the same JSON-RPC `ID`:
1. Caller A's `savedCallback` entry is silently replaced by Caller B's callback in the shared `h.savedCallbacks` map.
2. When the DON node responds to that `MessageId`, `handleWebAPITriggerMessage` finds and invokes Caller B's callback with the response payload that was actually produced for Caller A's request.
3. Caller A never receives a response (their HTTP request eventually times out with `RequestTimeoutError`), while Caller B — who did not initiate that particular workflow trigger — receives Caller A's response data.

This is a concrete cross-user response confusion / request-response hijack: an unprivileged client can obtain the response payload belonging to another user's request by choosing a colliding request ID, and can simultaneously deny service to the original requester. Depending on what the triggered workflow returns (which can include computed results derived from the caller's own inputs/credentials-adjacent context), this can leak response data across trust boundaries.

### Likelihood Explanation
The only precondition is that both requests hit the gateway HTTP endpoint for the same DON while the first one is still in flight (bounded by `CallbackMaxAgeSec`, default 120s) and use an identical `ID` string. There is no authentication segmentation, size restriction (other than max 200 chars), or uniqueness enforcement on the `ID` beyond gateway-level parsing. A malicious client can trivially pick a `MessageId` (e.g. `"1"`, `"request"`, or brute-force common values) and fire concurrent requests, or specifically target a known/predictable ID pattern used by a victim's client/library, making exploitation straightforward for any client capable of reaching the gateway's public endpoint.

### Recommendation
Scope the `savedCallbacks` key by both the calling client/session and the `MessageId` (similar to `requestCache`'s `{sender, id}` key), and/or reject requests whose `MessageId` collides with an existing in-flight entry (mirroring the `http_trigger_handler`'s "in-flight request" conflict behavior) instead of silently overwriting the map entry in `capabilities.handler.HandleLegacyUserMessage`.

### Proof of Concept
1. Client A sends a legacy Web API trigger JSON-RPC request to the gateway with `id = "X"`, targeting DON `D`. The gateway stores `savedCallbacks["X"] = callbackA` and forwards the request to DON `D`'s nodes.
2. Before DON `D` responds, Client B (unrelated/malicious) sends its own legacy trigger request to the same DON with the same `id = "X"`. The handler executes `h.savedCallbacks["X"] = &savedCallback{... Callback: callbackB}`, overwriting `callbackA` with no error or collision check (`core/services/gateway/handlers/capabilities/handler.go:411-420`).
3. DON `D`'s node responds with `MessageId = "X"` for Client A's original triggered request. `handleWebAPITriggerMessage` retrieves `savedCallbacks["X"]`, which is now `callbackB`, and calls `callbackB.SendResponse(...)` (`handler.go:148-162`).
4. Client B receives the JSON-RPC response intended for Client A's request; Client A's HTTP connection eventually returns a `RequestTimeoutError` from `gateway.ProcessRequest`'s `callback.Wait(ctx)` timeout path (`gateway.go:278-285`).

### Citations

**File:** core/services/gateway/gateway.go (L218-231)
```go
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/gateway.go (L250-273)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/common/message_util.go (L34-58)
```go
// ValidatedMessageFromReq validated and extracts a legacy Gateway Message
// from params field of JSON-RPC request
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

**File:** core/services/gateway/handlers/common/requestcache.go (L50-66)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L317-355)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```
