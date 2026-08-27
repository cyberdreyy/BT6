### Title
Attacker-controlled MessageId flood causes cross-user eviction of pending callbacks in `pruneCallbacks`, silently dropping a victim's legitimate node response - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores every incoming user request in a single shared `h.savedCallbacks` map keyed only by `MessageId`, with no per-user quota, allowlist, or rate limit ("TODO: apply allowlist and rate-limiting here"). An attacker who can reach the gateway's user HTTP endpoint can flood it with unique `MessageId`s to exceed `MaxSavedCallbacks`, causing `pruneCallbacks`'s oldest-first eviction to drop an older, still-pending, legitimate victim callback before its node response arrives.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go`) routes any legacy request (one with `DonId` set) straight to `h.HandleLegacyUserMessage(ctx, msg, callback)` after only `msg.Validate()` (basic field/shape validation, no per-user authentication, signature check, or allowlist), and there is no authorization gate on this class of request based on identity beyond DON ID selection. [1](#0-0) 

Inside `HandleLegacyUserMessage`, after minimal payload/timestamp checks, the handler unconditionally inserts the callback into the shared map keyed only by attacker-controlled `msg.Body.MessageId`, explicitly noting rate-limiting/allowlisting is not yet implemented: [2](#0-1) [3](#0-2) 

Periodically (every `CallbackPruneIntervalSec`, default 30s), `pruneCallbacks` removes expired entries, and if the map still exceeds `MaxSavedCallbacks` (default 20000), it sorts remaining entries by `createdAt` and deletes the oldest half — with no regard to which user or node submitted them: [4](#0-3) 

If a victim's request is submitted first (older `createdAt`) and the DON takes long enough to respond (up to `CallbackMaxAgeSec`, e.g. slow node, network delay, or heavy load), and an attacker floods more than `MaxSavedCallbacks` unique-MessageId requests in the interim, the victim's still-pending entry can be among the oldest half evicted. When the legitimate node response for the victim later arrives at `handleWebAPITriggerMessage`, the lookup `h.savedCallbacks[msg.Body.MessageId]` returns `found=false`, and the function silently returns `nil` without ever calling `savedCb.SendResponse`: [5](#0-4) 

Because the callback is never fulfilled, the victim's blocking `callback.Wait(ctx)` in `gateway.ProcessRequest` only returns via `ctx.Done()`, producing a spurious `RequestTimeoutError` instead of the real (successful) answer: [6](#0-5) [7](#0-6) 

No existing check (signature verification, allowlist, per-sender quota) prevents an attacker from generating arbitrarily many unique `MessageId`s and submitting them faster than the prune interval/DON response time, because the map has no per-identity bound — only a single global `MaxSavedCallbacks` cap shared across all users.

### Impact Explanation
This is a cross-user denial-of-response: an unprivileged, unauthenticated (or minimally authenticated) external client submitting ordinary trigger requests can cause another legitimate user's in-flight request to be silently dropped, degrading to a client-visible timeout even though the DON actually answered. This matches a cross-user response confusion / availability impact class (denial of service against specific victims via a shared, identity-agnostic resource), scoped to the gateway's web API trigger callback bookkeeping — it does not grant data disclosure or privilege escalation, but breaks the isolation invariant between users of the same DON/handler.

### Likelihood Explanation
Preconditions are low-cost for an attacker: they need only the ability to send `MethodWebAPITrigger` legacy messages to the gateway's user HTTP port faster than the prune interval and node round-trip time, generating > `MaxSavedCallbacks` (default 20000) unique `MessageId`s. The code explicitly documents that allowlisting/rate-limiting for this path is not yet implemented (`// TODO: apply allowlist and rate-limiting here`), so no additional privilege or bypass is required beyond normal ability to call the endpoint. The attack is repeatable and does not require precise timing beyond outrunning the victim's DON response and the periodic prune cycle.

### Recommendation
Add per-sender/per-identity quotas (or rate limiting) on `HandleLegacyUserMessage` before insertion into `savedCallbacks`, independent of the global `MaxSavedCallbacks` cap, so that one sender cannot exhaust capacity shared with other users. Additionally, consider keying/bounding capacity per requester (e.g., IP, auth token, or sender address) and/or making `pruneCallbacks` fail loudly (return an error/response to the evicted callback) rather than silently discarding it, so a dropped callback resolves quickly rather than waiting for full `ctx` timeout.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with a small `MaxSavedCallbacks` (e.g., 4) and `CallbackMaxAgeSec` large enough that nothing expires.
2. Insert a "victim" callback first via direct manipulation of `h.savedCallbacks` (or via `HandleLegacyUserMessage` with a controllable callback) with an old `createdAt`.
3. Insert N additional "attacker" callbacks (N > MaxSavedCallbacks) with `createdAt` after the victim's.
4. Call `h.pruneCallbacks()` directly.
5. Assert the victim's `MessageId` is no longer present in `h.savedCallbacks`.
6. Simulate the DON's legitimate response for the victim by calling `h.handleWebAPITriggerMessage(ctx, victimRespMsg, nodeAddr)` and assert it returns `nil` without invoking `SendResponse` on the victim's callback (e.g., via a mock `handlers.Callback` whose `SendResponse` records whether it was called).
7. Assert that a call to the victim's `callback.Wait(ctx)` (with a short-lived `ctx`) returns only via `ctx.Err()` (timeout), not with a payload, confirming the response is silently lost.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L314-334)
```go
	// If there are still too many callbacks, sort them by creation time and remove the oldest ones.
	maxSize := h.config.MaxSavedCallbacks
	var evicted int
	if len(h.savedCallbacks) > maxSize {
		type entry struct {
			id        string
			createdAt time.Time
		}
		entries := make([]entry, 0, len(h.savedCallbacks))
		for id, cb := range h.savedCallbacks {
			entries = append(entries, entry{id, cb.createdAt})
		}
		sort.Slice(entries, func(i, j int) bool {
			return entries[i].createdAt.Before(entries[j].createdAt)
		})
		// Trim to maxSize/2 to avoid sorting the list too frequently.
		for _, e := range entries[:len(entries)-maxSize/2] {
			delete(h.savedCallbacks, e.id)
			evicted++
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/callback.go (L28-38)
```go
func (c *Callback) Wait(ctx context.Context) (handlers.UserCallbackPayload, error) {
	if !c.waitCalled.CompareAndSwap(false, true) {
		return handlers.UserCallbackPayload{}, errors.New("Wait can only be called once per Callback instance")
	}
	select {
	case <-ctx.Done():
		return handlers.UserCallbackPayload{}, ctx.Err()
	case r := <-c.ch:
		return r, nil
	}
}
```
