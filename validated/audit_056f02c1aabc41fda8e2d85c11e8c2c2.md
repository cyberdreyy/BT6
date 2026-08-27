### Title
Attacker-controlled `MessageId` allows cross-user response hijacking in the Gateway `WebAPIHandler` legacy trigger path - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` stores an in-flight user callback in a shared map keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to the sender/session. Any unprivileged client can submit a request whose `MessageId` collides with another user's in-flight request, silently overwriting the stored callback and redirecting the DON's eventual response to the attacker instead of the legitimate requester.

### Finding Description
`HandleLegacyUserMessage` validates only that the payload can be decoded and that its `Timestamp` is not older than `MaxAllowedMessageAgeSec` [1](#0-0) . It then stores the caller's callback in the shared `h.savedCallbacks` map using `msg.Body.MessageId` as the key, without any allowlist/rate-limit check yet applied (explicitly marked as `// TODO: apply allowlist and rate-limiting here`) and without verifying uniqueness or binding the entry to the sender: [2](#0-1) 

When a DON node later responds via `HandleNodeMessage` → `handleWebAPITriggerMessage`, the gateway looks up and deletes the callback purely by `MessageId` and forwards the raw response to whichever callback is currently stored under that key: [3](#0-2) 

Because `MessageId` originates from the untrusted, unprivileged client request and the map is shared across all users of the DON/handler, nothing prevents one user from choosing (or guessing/observing) a `MessageId` value already in flight for another user's legitimate request. If the attacker's message with the same `MessageId` arrives before the legitimate response, the map entry is overwritten (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`), so the subsequent DON response — intended for the original caller — is instead delivered to the attacker's callback. This is a direct analog of the audit's stale/insufficiently-bounded check enabling a trust violation: here the missing check is "uniqueness/ownership of MessageId," analogous to the missing bound on staleness, and the impact is the same class of "silently wrong data delivered to the wrong party."

### Impact Explanation
This allows a completely unprivileged remote caller to intercept responses (including web API trigger/target/compute results routed through the DON) destined for another user's request, i.e. cross-user response confusion. Depending on what capability output is being relayed (e.g., HTTP body content originating from workflow triggers), this can leak data intended for another caller to an attacker-controlled client callback.

### Likelihood Explanation
Likelihood depends on the attacker being able to predict or race a `MessageId` used by a legitimate concurrent request. If `MessageId` values are not cryptographically random/high-entropy or are otherwise observable/guessable by the client population (e.g., sequential, workflow-derived, or reused across retries), the collision is straightforward to engineer, especially since the code notes rate-limiting/allowlisting is not yet applied at this stage, making it easy to send many messages to force a collision window with a real request.

### Recommendation
Scope `savedCallbacks` entries by both `MessageId` and the authenticated sender/DON identity (or use a server-generated, unpredictable correlation ID that is never client-supplied), and reject/log attempts to overwrite an existing, non-expired callback entry rather than clobbering it silently.

### Proof of Concept
1. Legitimate user A sends a `web_api_trigger` message with `MessageId = "X"`; the gateway stores `savedCallbacks["X"] = callbackA` at [4](#0-3) .
2. Before the DON responds, attacker B sends their own `web_api_trigger` message also using `MessageId = "X"` (client controls this field and it passes the payload/timestamp checks). The map entry is overwritten: `savedCallbacks["X"] = callbackB`.
3. The DON eventually replies referencing `MessageId = "X"`. `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds `callbackB`, deletes the entry, and sends the response — which was computed for A's request — to B's callback via `savedCb.SendResponse(...)` at [5](#0-4) .
4. User A never receives a response (silent failure), while attacker B has received data/response intended for A.

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
