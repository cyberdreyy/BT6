## Analog Finding

### Title
Unauthenticated `MessageId` collision in Gateway `savedCallbacks` map allows cross-user response hijacking / DoS - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `ProtocolFeeHook.postDispatch()` bug allows an unprivileged caller to pre-register an arbitrary `messageId` in a validation mapping before the legitimate message arrives, causing the real message to be rejected (DoS). The Chainlink Gateway's legacy Web API capability handler has the same root cause: any unauthenticated HTTP client hitting the gateway's public `/` endpoint fully controls the JSON-RPC `id` that becomes the `MessageId` key used to register a pending callback, and the handler blindly overwrites `savedCallbacks[MessageId]` without checking whether that key is already in use by another in-flight request.

### Finding Description
When a user posts a legacy request to the Gateway HTTP endpoint, `gateway.ProcessRequest` decodes the untrusted `jsonRequest.ID` and only enforces a length cap (`<= 200` chars) — it is never checked for uniqueness or scoped to a session/caller: [1](#0-0) 

That `ID` is carried through unchanged into the internal `api.Message` as `MessageId`: [2](#0-1) 

`HandleLegacyUserMessage` then stores the caller's callback keyed solely by this attacker-controlled `MessageId`, with no check for an existing/pending entry, and unconditionally overwrites whatever was already there: [3](#0-2) 

Later, when a DON node's response for that same `MessageId` arrives, `handleWebAPITriggerMessage` looks the ID up in `savedCallbacks`, deletes it, and delivers the response to whichever callback currently occupies that slot: [4](#0-3) 

Because the map is keyed only by client-supplied `MessageId` (no per-session/per-caller namespacing, no "already exists" check before insert), any unprivileged HTTP caller can pick the same `id` as another in-flight legitimate request and silently overwrite its callback registration — exactly analogous to `postDispatch()` letting anyone pre-mark a `messageId` before the legitimate message is processed.

### Impact Explanation
If an attacker submits a request using an `id` equal to a currently pending legitimate request's `id` (within the `CallbackMaxAgeSec` window, default 120s), the original caller's callback entry is overwritten. When the DON eventually responds with that `MessageId`, the response is routed to the attacker's callback instead of the legitimate caller's, while the legitimate caller's HTTP request stalls until `callback.Wait(ctx)` times out and returns a "handler timeout" error. This is both a denial-of-service of message delivery to the legitimate caller and a cross-user response confusion (the attacker receives the response payload intended for another user's request).

### Likelihood Explanation
The gateway's public HTTP endpoint is unauthenticated at this layer (only downstream JWT/allowlist checks apply to specific newer handlers, not to this legacy path), and `id` values are entirely client-chosen with no server-side uniqueness enforcement. An attacker only needs to guess or observe an `id` in flight (e.g., by controlling one of the two colliding requests, or racing predictable/sequential IDs) to trigger the collision, making this readily exploitable by any network client capable of reaching the Gateway's user-facing HTTP port.

### Recommendation
Do not trust client-supplied `id`/`MessageId` values as an exclusive routing key for a shared, cross-request callback map. Either:
- Reject registration if `savedCallbacks[MessageId]` already exists (return an error to the second caller instead of overwriting), or
- Generate a server-side unique correlation ID (independent of the attacker-controlled JSON-RPC `id`) for internal callback routing, mapping the external `id` back only for the final response.

### Proof of Concept
1. Legitimate user U sends `POST /` to the Gateway with JSON-RPC `id: "X"`, `method: "web_api_trigger"`, targeting DON `D`. This calls `HandleLegacyUserMessage`, which stores `savedCallbacks["X"] = U's callback` and forwards the request to DON nodes.
2. Before DON `D` responds (within the 120s callback TTL), attacker A sends their own `POST /` request to the same DON with the same JSON-RPC `id: "X"`. `HandleLegacyUserMessage` overwrites `savedCallbacks["X"]` with A's callback, per [5](#0-4) .
3. When a DON node later responds with `MessageId: "X"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds A's callback (not U's), deletes the entry, and delivers the response to A.
4. U's original HTTP request never receives U's intended response and eventually fails with a gateway "handler timeout" error, while A silently receives a response correlated to U's original request.

### Citations

**File:** core/services/gateway/gateway.go (L218-232)
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
	var isLegacyRequest = false
```

**File:** core/services/gateway/handlers/common/message_util.go (L46-52)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
