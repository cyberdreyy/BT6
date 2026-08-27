### Title
Attacker with a valid signing key can flood `savedCallbacks` to force `pruneCallbacks` to evict another user's pending trigger callback, causing a cross-user denial of response - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The capabilities `handler.HandleLegacyUserMessage` accepts any signed `web_api_trigger` message and unconditionally inserts an entry into the shared `h.savedCallbacks` map, with no per-sender quota, allowlist, or rate limit (explicitly marked `// TODO: apply allowlist and rate-limiting here`). When the map exceeds `h.config.MaxSavedCallbacks`, `pruneCallbacks` blindly evicts the oldest half of *all* entries by `createdAt`, regardless of which user/sender owns them, silently dropping any still-pending legitimate callback with no error or response ever delivered to that caller.

### Finding Description
`HandleLegacyUserMessage` validates payload shape/timestamp/method, but performs no sender-based authorization, allowlisting, or per-user quota before storing the callback: [1](#0-0) [2](#0-1) 

Any request that passes `msg.Validate()` (a valid self-consistent signature over `msg.Body`, i.e. any attacker with a valid signing key, such as an external-initiator credential holder) reaches this code path via `gateway.ProcessRequest`, which routes legacy DON-ID requests directly to `HandleLegacyUserMessage`: [3](#0-2) 

Because `msg.Body.MessageId` is attacker-controlled and unique per request, an attacker can submit `MaxSavedCallbacks + 1` (default 20000) requests rapidly, each creating a new `savedCallback` entry keyed by its own `MessageId`, all timestamped after any pre-existing legitimate entry.

`pruneCallbacks` sorts all entries in the map by `createdAt` (irrespective of sender identity) and deletes the oldest half once the map exceeds `MaxSavedCallbacks`: [4](#0-3) 

This deletion path does not call `SendResponse` on the evicted callback - it is a bare `delete(h.savedCallbacks, e.id)`. The corresponding user's `gateway.ProcessRequest` goroutine, blocked on `callback.Wait(ctx)`, will never see the real node response even if the DON later replies (since `HandleNodeMessage` looks the ID up in `h.savedCallbacks` and finds nothing after eviction), and will only recover via the caller's own context deadline into a generic timeout error: [5](#0-4) 

This behavior is directly demonstrated by the existing unit test, which shows only the newest entry survives regardless of which "user" it belongs to: [6](#0-5) 

The root cause is that eviction is a purely global, age-based LRU-style trim with no per-sender fairness/quota, combined with the complete absence of the still-unimplemented rate limiting/allowlisting on this handler's entry point.

### Impact Explanation
This breaks the isolation invariant between users of the gateway: an unprivileged holder of any valid signing credential can silently discard another legitimate, already in-flight user's pending trigger response, forcing that request to fail with a timeout instead of returning the correct DON result. This is a cross-user denial-of-response / DoS on the gateway's request-handling path, achievable purely with the volume of self-authored trigger requests, without any node, DON, or admin compromise.

### Likelihood Explanation
The only precondition is possession of a signing key capable of producing a `msg.Validate()`-passing signed message (matching the "external-initiator credential holder" threat actor) and the ability to send `MaxSavedCallbacks + 1` requests before the `CallbackPruneIntervalSec` tick (default 30s) or before invoking prune. No rate limiting currently exists on this code path (per the explicit TODO), so this is straightforward and repeatable for any attacker capable of generating load, e.g. with a simple script issuing many `web_api_trigger` requests with unique `MessageId`s.

### Recommendation
- Implement the pending TODO: per-sender rate limiting and/or allowlisting before inserting into `savedCallbacks`.
- Change `pruneCallbacks` eviction to be per-sender fair (e.g., cap outstanding callbacks per sender/topic) rather than a single global age-sorted trim, so one sender cannot starve another's slot.
- Consider rejecting new requests (with an explicit error response) once `MaxSavedCallbacks` is reached instead of silently evicting older, potentially still-valid entries belonging to other users.

### Proof of Concept
Go unit test plan (extends `handler_test.go`):
1. Configure `handler.config.MaxSavedCallbacks = N` (small, e.g. 10).
2. Seed `handler.savedCallbacks["victim"] = &savedCallback{id: "victim", createdAt: now.Add(-10*time.Second), Callback: victimCb}` representing a legitimate still-pending user request (earliest timestamp).
3. Call `handler.HandleLegacyUserMessage` (or directly populate the map to simulate) with `N` attacker-crafted signed trigger messages, each with a unique `MessageId` and `createdAt` newer than `victim`.
4. Call `handler.pruneCallbacks()`.
5. Assert `handler.savedCallbacks` no longer contains `"victim"`.
6. Assert `victimCb.Wait(ctx)` returns a context-deadline error (never received `SendResponse`), proving the legitimate caller's response was silently dropped without any error signal from the handler itself.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L518-539)
```go
	t.Run("enforces max size by evicting oldest", func(t *testing.T) {
		// maxSize=2: trims to maxSize/2=1, so only the newest entry survives
		handler.config.MaxSavedCallbacks = 2

		handler.mu.Lock()
		handler.savedCallbacks = make(map[string]*savedCallback)
		now := time.Now()
		handler.savedCallbacks["a"] = &savedCallback{id: "a", createdAt: now.Add(-3 * time.Second)}
		handler.savedCallbacks["b"] = &savedCallback{id: "b", createdAt: now.Add(-2 * time.Second)}
		handler.savedCallbacks["c"] = &savedCallback{id: "c", createdAt: now.Add(-1 * time.Second)}
		handler.savedCallbacks["d"] = &savedCallback{id: "d", createdAt: now}
		handler.mu.Unlock()

		handler.pruneCallbacks()

		handler.mu.Lock()
		require.Len(t, handler.savedCallbacks, 1)
		require.Contains(t, handler.savedCallbacks, "d")
		handler.mu.Unlock()

		handler.config.MaxSavedCallbacks = defaultMaxSavedCallbacks
	})
```
