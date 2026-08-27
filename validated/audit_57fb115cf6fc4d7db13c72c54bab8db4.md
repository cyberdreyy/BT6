### Title
Unauthenticated flood of `HandleLegacyUserMessage` requests causes `pruneCallbacks` to evict a victim's pending, non-expired `savedCallback`, dropping their DON response - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores every incoming user trigger request in the shared `h.savedCallbacks` map keyed by the attacker-controlled `MessageId`, with an explicit `// TODO: apply allowlist and rate-limiting here` comment showing no sender/rate-limit gating is implemented at this layer. Because `pruneCallbacks` evicts the oldest half of all entries once the map exceeds `MaxSavedCallbacks`, an attacker who can reach the gateway's legacy HTTP endpoint can flood it with many distinct-`MessageId` requests to force eviction of a victim's still-pending, non-expired callback before the DON responds.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) validates payload shape/timestamp/method only; it does **not** apply any per-sender rate limit or allowlist before inserting into the shared map: [1](#0-0) 
```
// TODO: apply allowlist and rate-limiting here
if msg.Body.Method != MethodWebAPITrigger {
```
Every valid-shaped message (attacker-controlled `MessageId`) is unconditionally stored: [2](#0-1) 

`pruneCallbacks` runs periodically (`Start`, ticker of `CallbackPruneIntervalSec`, default 30s) and, when `len(savedCallbacks) > maxSize`, sorts all entries by `createdAt` and deletes `entries[:len(entries)-maxSize/2]` — i.e. it blindly evicts the oldest half regardless of whether they are legitimate, still-pending, and non-expired: [3](#0-2) 

The existing test `TestPruneCallbacks/enforces_max_size_by_evicting_oldest` confirms this oldest-first, no-ownership-check eviction behavior: [4](#0-3) 

When the DON node eventually responds for the evicted `MessageId`, `handleWebAPITriggerMessage` looks up the map, finds nothing (`found == false`), and silently returns `nil` — the response is dropped and `Callback.SendResponse` is never invoked for the victim: [5](#0-4) 

Attack flow: an attacker with only network access to the gateway HTTP endpoint sends more than `MaxSavedCallbacks` (default 20000, or lower if the DON operator configures a smaller value) well-formed `web_api_trigger` requests, each with a unique `MessageId`, within the callback lifetime window (`CallbackMaxAgeSec`, default 120s) and before the next `pruneCallbacks` tick. A victim's legitimate request submitted just before the flood remains in the map (not yet expired) but, being one of the oldest entries, is included in the evicted half. The victim's `callback.Wait(ctx)` in `gateway.go` (`core/services/gateway/gateway.go:278`) will then time out rather than receive the DON's real response, since `handleWebAPITriggerMessage` cannot find the entry to complete it.

### Impact Explanation
This is a Denial-of-Service against individual legitimate gateway users: an unauthenticated attacker can selectively cause dropped/timed-out responses for other users' in-flight trigger requests by exhausting the shared, unbounded-by-authentication `savedCallbacks` map. This matches an availability/DoS impact class rather than fund loss or authentication bypass, but it is a concrete, attacker-triggerable cross-user disruption enabled by a lack of rate limiting/allowlisting explicitly marked as an unimplemented TODO in the code.

### Likelihood Explanation
- Precondition: only network reachability to the gateway's legacy HTTP endpoint is required; no credentials, signature validity beyond basic message shape, or DON/operator privileges are needed (the TODO comment confirms sender/allowlist/rate-limit checks are not yet implemented for this handler).
- The attacker must generate `MaxSavedCallbacks` (or `MaxSavedCallbacks - existing legit entries`) well-formed, non-stale, correctly-signed-enough messages with unique `MessageId`s inside the pruning/age window — feasible via scripted flooding, especially since `MaxSavedCallbacks` can be configured smaller than the default 20000 by DON operators, and the default callback age (120s) / prune interval (30s) gives a fairly short window to win the race but is repeatable indefinitely.
- No global gateway-level rate limiting was found gating `HandleLegacyUserMessage` calls before they reach this code path (only `handleWebAPIOutgoingMessage`, which handles DON→client traffic, calls `nodeRateLimiter.Allow`).

### Recommendation
- Implement the still-outstanding TODO: enforce per-sender rate limiting and/or an allowlist on `HandleLegacyUserMessage` before inserting into `savedCallbacks`.
- Bound `savedCallbacks` growth per-sender (e.g., cap concurrent in-flight callbacks per sender) instead of relying solely on a global oldest-first eviction.
- Consider rejecting new inserts (backpressure) rather than silently evicting older, still-valid entries when the map is at capacity, or prioritize eviction of the same attacker's own entries over other senders'.

### Proof of Concept
Go unit test plan (extending `handler_test.go`):
1. In `TestPruneCallbacks`, set `handler.config.MaxSavedCallbacks = N` (e.g., 10).
2. Insert one legitimate "victim" callback via `handler.HandleLegacyUserMessage` with `MessageId = "victim"`, backed by a `hc.NewCallback()` whose `Wait` is later checked.
3. Insert `N` additional distinct-`MessageId` "attacker" callbacks via repeated `HandleLegacyUserMessage` calls (simulating flooding), all created after the victim's, so the victim is oldest.
4. Call `handler.pruneCallbacks()` directly (or wait for the ticker in `Start`).
5. Assert `handler.savedCallbacks` no longer contains `"victim"` (`require.NotContains`).
6. Simulate the DON's legitimate response arriving late for `"victim"` via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`, and assert the victim's `Callback.SendResponse` is never invoked (e.g., `cbVictim.Wait` times out or returns no response), proving the response is dropped.

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
