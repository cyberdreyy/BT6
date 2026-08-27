### Title
Cross-user response hijacking via attacker-controlled `MessageId` collision in Web API trigger handler - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The gateway's legacy Web API trigger handler stores a pending client callback in a shared, in-memory map keyed **only** by the client-supplied `MessageId`, with no binding to the requester's identity or session. An unprivileged client can submit a trigger request carrying the same `MessageId` as another pending (in-flight) request, silently overwriting the original callback entry. When a capability node later responds with that `MessageId`, the response is delivered to whichever callback currently occupies the map slot — potentially the attacker's connection instead of the legitimate requester's, or vice versa.

### Finding Description
Incoming legacy trigger requests are validated only for length/format of `MessageId`, not uniqueness or ownership: [1](#0-0) 

The handler unconditionally stores (overwriting any existing entry) a callback keyed by this attacker-influenced ID, with no check that the ID is unused or bound to the calling sender: [2](#0-1) 

When any DON node later sends back a `web_api_trigger` response for that `MessageId`, the handler looks the ID up in the shared map, deletes the entry, and forwards the response to whatever callback is currently stored there — with no verification that it belongs to the same client that originated the corresponding request: [3](#0-2) 

This is directly reachable from an unauthenticated/unprivileged external client via the gateway's public HTTP entrypoint, which routes legacy requests straight into this handler: [4](#0-3) 

This mirrors the root cause pattern in the `FighterFarm.reRoll()` finding: a value fully controllable/predictable by an unprivileged actor (`MessageId`, analogous to `tokenId`/`numRerolls`) is used as the sole key that determines which actor receives a sensitive outcome, without any binding to the correct owner/session — enabling one user's request/response state to collide with another's.

### Impact Explanation
An attacker can:
1. Predict or learn a victim's in-flight `MessageId` (e.g., if IDs are short, sequential, reused, or leaked via logs/timing), then send a colliding trigger request to overwrite the victim's `savedCallbacks` entry with the attacker's own callback. This causes the victim's legitimate node response (which may contain data resulting from the victim's authorized action) to be delivered to the attacker instead — a cross-user response confusion / potential information disclosure.
2. Conversely, an attacker's own response could be misdirected to the victim, causing confusing or spoofed results delivered under the victim's request.

Because `savedCallbacks` is shared across all clients of a DON handler with no per-sender partitioning, this breaks the intended request/response isolation between unrelated, unprivileged users of the same gateway.

### Likelihood Explanation
Exploitability depends on the attacker being able to guess, observe, or force reuse of another client's `MessageId`. Since `MessageId` is entirely client-supplied (up to 200 chars, only checked for non-null-suffix and length) and not required to be cryptographically random or session-bound, callers that generate short/predictable/sequential IDs (or IDs derivable from public workflow metadata) are at risk. This does not require any node compromise, peer collusion, or privileged access — only the ability to send ordinary requests to the internet-facing gateway.

### Recommendation
Bind `savedCallbacks` entries to the authenticated/identified requester (e.g., a signed session token, source connection, or DON-scoped nonce) in addition to `MessageId`, and reject/namespace collisions rather than silently overwriting a still-pending entry. Consider requiring the gateway to generate (or salt) the message correlation ID server-side rather than trusting a fully client-controlled value for map keying.

### Proof of Concept
1. Client A sends a legacy `web_api_trigger` request through the gateway HTTP endpoint with `MessageId = "X"`; the handler stores A's callback under key `"X"` in `savedCallbacks`.
2. Before a node responds, Client B (attacker, unprivileged, unrelated to A) sends its own legacy trigger request also using `MessageId = "X"`. The handler overwrites the map entry, replacing A's callback with B's.
3. When the DON node responds to the original request tagged `MessageId = "X"` (intended for A), `handleWebAPITriggerMessage` looks up `"X"` in `savedCallbacks`, finds B's callback, deletes the entry, and delivers the response to B — not A. [5](#0-4)

### Citations

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
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
