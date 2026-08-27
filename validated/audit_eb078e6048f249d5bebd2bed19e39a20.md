### Title
Unbounded `savedCallbacks` map allows one user's message flood to prematurely evict another user's still-valid callback before `CallbackMaxAgeSec` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.pruneCallbacks` evicts entries strictly by insertion time (`createdAt`), removing the oldest half of the map whenever `len(savedCallbacks) > MaxSavedCallbacks`, with no per-user quota or partitioning. Because `HandleLegacyUserMessage` explicitly has no rate-limiting or allowlist applied yet (`// TODO: apply allowlist and rate-limiting here`), an unauthenticated/unprivileged caller can flood `web_api_trigger` messages to push the shared map size above `MaxSavedCallbacks`, causing a victim's earlier, still-unexpired callback to be evicted before its `CallbackMaxAgeSec` TTL elapses.

### Finding Description
Each incoming `web_api_trigger` legacy user message is stored in the shared `h.savedCallbacks` map keyed by `msg.Body.MessageId`, with `createdAt` set to `time.Now()` at insertion [1](#0-0) . There is no per-sender/per-user isolation in this map or in the pruning logic. The prune routine, run on a timer, first removes expired entries by TTL, then—if the map still exceeds `MaxSavedCallbacks`—sorts all remaining entries by `createdAt` and deletes the oldest half regardless of whose request they belong to [2](#0-1) .

Critically, the ingestion path that populates this map, `HandleLegacyUserMessage`, contains an explicit TODO acknowledging the absence of allowlisting/rate-limiting for this request type: [3](#0-2) . Combined with the shared, size-capped map, this means any client able to submit signed `web_api_trigger` messages can flood the gateway with many low-effort requests in a short window. Once the map exceeds `MaxSavedCallbacks` (default 20000, `defaultMaxSavedCallbacks`), the next prune cycle deletes the oldest half by timestamp—which can include a victim's legitimately older but still-valid (pre-TTL) callback—causing that victim's request to silently receive no response (the node's later `web_api_trigger` response for that `messageId` finds nothing in `savedCallbacks` and is dropped in `handleWebAPITriggerMessage`) [4](#0-3) .

However, the specific "gaming the sort order" framing in the question does not hold: `createdAt` is set by the server (`time.Now()`) at the moment of insertion, not attacker-controlled input, so an attacker cannot arbitrarily manipulate which bucket ("oldest half") their own vs. the victim's entries fall into beyond simply timing when they submit relative to the victim. The real, valid issue is a straightforward flood/DoS via lack of quota/rate-limiting on this shared, bounded resource, not a "gameable eviction sort."

### Impact Explanation
This is a denial-of-service against a specific victim's in-flight request/response correlation: a flood of attacker messages can cause a victim's earlier `web_api_trigger` callback entry to be evicted and dropped before the corresponding DON node response arrives, so the victim never receives their response (the gateway silently discards it, per `handleWebAPITriggerMessage`'s `found` check). This does not leak secrets or cause cross-user data confusion (the map is keyed by unguessable `MessageId`, so an attacker cannot read or intercept another user's response), but it does violate availability/isolation expectations between tenants sharing a gateway/DON. This maps to a limited availability-impact / resource-exhaustion class rather than authentication bypass, privilege escalation, or data disclosure.

### Likelihood Explanation
Exploitability requires only the ability to send enough valid signed `web_api_trigger` gateway messages to exceed `MaxSavedCallbacks` (20000 by default) within one `CallbackPruneIntervalSec` window (default 30s) while the victim's request is still pending — feasible for any unprivileged client able to submit gateway requests, especially since the code itself documents that no rate-limiting/allowlist is yet applied on this path. Precise targeting of a specific victim's request is not reliable (since attacker cannot control `createdAt` ordering deterministically relative to victim beyond rough timing), but a coarse-grained, repeatable DoS causing widespread eviction of legitimate pending callbacks (not merely the intended victim) is plausible and repeatable under sustained flooding.

### Recommendation
- Implement per-sender/per-user rate limiting and/or allowlisting on `HandleLegacyUserMessage` as the existing TODO indicates, before entries are admitted into `savedCallbacks`.
- Consider partitioning quota or using per-user/per-DON-member caps within `savedCallbacks` so one requester's volume cannot exhaust the shared eviction budget for others.
- Alternatively/also, prefer TTL-only eviction (drop only genuinely expired entries) and reject new insertions with backpressure/error when at capacity, rather than evicting other unexpired users' entries.

### Proof of Concept
Go handler-level integration test plan:
1. Configure `handler` with small `MaxSavedCallbacks` (e.g., 10) and `CallbackMaxAgeSec` large enough that a victim entry should not expire naturally during the test.
2. Insert one "victim" callback via `HandleLegacyUserMessage` (or directly via `h.savedCallbacks`) with a mock `handlers.Callback` capturing whether `SendResponse` is called.
3. Immediately after, insert `MaxSavedCallbacks` additional "attacker" callbacks with `createdAt` timestamps later than the victim's (simulating burst flooding), pushing the map size past threshold.
4. Call `h.pruneCallbacks()` directly.
5. Assert that the victim's entry has been deleted from `h.savedCallbacks` even though `now.Sub(victim.createdAt) < CallbackMaxAgeSec`.
6. Simulate the DON node's `web_api_trigger` response for the victim's `MessageId` arriving via `handleWebAPITriggerMessage` and assert the victim's mock callback's `SendResponse` is never invoked (silently dropped), confirming the victim never receives a response despite submitting before TTL expiry.

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
