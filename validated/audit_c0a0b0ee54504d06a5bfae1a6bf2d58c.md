## Analysis

The Sherlock bug (`M-6`) is a class of vulnerability where an unprivileged, adversarial actor keeps a victim's identifier "locked"/pending indefinitely by controlling a state key that multiple parties share, causing responses/state transitions for the victim to be silently swallowed or misdirected.

The closest reachable analog in this chainlink repo is in the Gateway's legacy Web API capability handler, `core/services/gateway/handlers/capabilities/handler.go`.

### Title
Client-controlled `MessageId` collision in Gateway `HandleLegacyUserMessage` allows an unprivileged user to hijack/trap another user's pending callback - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The Gateway's legacy web-API-trigger path keys its pending-response cache (`savedCallbacks`) purely by the client-supplied `msg.Body.MessageId` field, with no per-sender namespacing and no uniqueness enforcement against other in-flight requests.

### Finding Description
When an end user submits a legacy trigger request, `gateway.ProcessRequest` decodes and validates the message (only length/format checks, no uniqueness check) and calls `HandleLegacyUserMessage`, which stores the caller's callback keyed solely by the attacker-controllable `MessageId`: [1](#0-0) 

`MessageId` comes straight from the signed request body and is only checked for length/format in `Message.Validate()`, not for collision with other pending requests: [2](#0-1) [3](#0-2) 

When a node eventually responds, the handler looks the callback up by `MessageId` alone and delivers the response to whichever callback currently occupies that map slot, deleting the entry so only the first response wins: [4](#0-3) 

Because `msg.Body.MessageId` is fully attacker-controlled and there is no per-sender scoping, uniqueness check, or ownership binding, a malicious client can submit a request using the same `MessageId` as a victim's currently in-flight request. This overwrites `h.savedCallbacks[id]` with the attacker's callback (`h.mu.Lock(); h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`), so:
- The victim's original callback reference is discarded/orphaned — the victim's request is now "trapped" and will never receive a response (it will only time out via the gateway's `callback.Wait(ctx)` deadline), analogous to the FrankenDAO delegatee being perpetually locked by another actor's action.
- When the DON node's real response for that `MessageId` arrives, it is delivered to the attacker's callback instead of the victim's — a cross-user response confusion/hijack.

The code even has an explicit acknowledgment that authorization is missing for this path: [5](#0-4) 

### Impact Explanation
An unauthenticated/unprivileged gateway client can:
1. Deny service to a targeted victim request by causing it to hang until the gateway's request timeout (denial-of-service/"trap", matching the bug class's core impact).
2. Receive a response payload that was intended for another user's request (cross-user response confusion), which may leak details of the victim's trigger execution result depending on the payload contents.

This is reachable purely from the internet-facing gateway HTTP endpoint by any client able to sign and submit a legacy message — no privileged role is required, satisfying the "unprivileged-actor" and "internet-facing gateway" scope.

### Likelihood Explanation
Exploitation requires only knowledge/guessing of a `MessageId` value that collides with an in-flight victim request. `MessageId` is a client-chosen string (up to `MessageIdMaxLen`) with no server-side randomness requirement, so if any part of the ecosystem uses predictable or short/sequential IDs (e.g., timestamps, incrementing counters used by some SDKs), collision is trivial to force; even with random UUIDs, an attacker who can observe or predict a victim's `MessageId` (e.g., via logs, shared automation, or a victim reusing IDs) can win the race. The narrow window is bound by the `CallbackMaxAgeSec` (default 120s) during which the victim's request remains outstanding.

### Recommendation
Scope `savedCallbacks` keys by `(sender, MessageId)` rather than `MessageId` alone, and/or reject a new `HandleLegacyUserMessage` call whose `MessageId` already exists in `savedCallbacks` (returning a "duplicate/for another request" error) instead of silently overwriting the pending entry. Additionally, implement the still-outstanding "apply allowlist and rate-limiting here" TODO so anonymous requests cannot interact with the shared callback cache at all.

### Proof of Concept
1. Victim signs and submits a legacy `web_api_trigger` message with `MessageId = "X"` to the gateway; `HandleLegacyUserMessage` stores victim's callback under key `"X"` and forwards the request to DON nodes.
2. Before the DON responds, attacker (any client capable of signing a message, no special privilege) submits their own legacy message reusing `MessageId = "X"`. This call again reaches `h.mu.Lock(); h.savedCallbacks["X"] = &savedCallback{attackerCallback}` [1](#0-0) , overwriting the victim's stored callback.
3. When a node later returns a response for `MessageId = "X"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]` — now the attacker's callback — and delivers the (possibly victim-destined) response to the attacker, while the victim's original `callback.Wait(ctx)` in `gateway.ProcessRequest` blocks until timeout and then returns a generic timeout error [6](#0-5) .

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/gateway.go (L278-285)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
```
