### Title
Oldest-first eviction in `pruneCallbacks` lets a request-flooding attacker evict other users' pending `web_api_trigger` callbacks (silent drop) - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`pruneCallbacks` evicts callbacks purely by creation-time order (`entries[:len(entries)-maxSize/2]`) once `len(savedCallbacks) > MaxSavedCallbacks`, with no per-sender/per-user accounting. Any client able to enqueue enough `web_api_trigger` requests before the DON responds can push the map over `MaxSavedCallbacks`, causing the oldest ~half of *all* pending callbacks — including other users' in-flight requests — to be silently deleted without ever calling `SendResponse`.

### Finding Description
`HandleLegacyUserMessage` stores every incoming trigger under `h.savedCallbacks[msg.Body.MessageId]` keyed only by message ID, with no sender/user field to protect isolation [1](#0-0) . The periodic `pruneCallbacks` first deletes expired entries by age, then — if the map still exceeds `MaxSavedCallbacks` — sorts remaining entries by `createdAt` and deletes the oldest `len(entries)-maxSize/2`, i.e. it evicts by insertion order regardless of whose request it is [2](#0-1) . Evicted callbacks are simply removed from the map; they never receive a response (no error is sent back), so the caller experiences a silent hang until its own client-side timeout, whereas the attacker's own more-recently-created callbacks survive since they sort last. Critically, `HandleLegacyUserMessage` (the legacy v1 path this `pruneCallbacks` belongs to) contains an explicit `TODO: apply allowlist and rate-limiting here` [3](#0-2)  — there is no per-sender rate limiter on the incoming user-request path in this handler (the `nodeRateLimiter` only guards the DON→gateway outgoing path in `handleWebAPIOutgoingMessage`) [4](#0-3) . Thus an unauthenticated/unprivileged caller of this legacy handler can burst far more than `MaxSavedCallbacks/2` trigger requests before the next 30-second prune tick, guaranteeing older, legitimate victim callbacks get evicted first.

### Impact Explanation
Impact is a cross-user denial-of-response: a victim's `web_api_trigger` request is silently dropped (never receives `SendResponse`) purely because an unrelated attacker generated enough newer requests to exceed `MaxSavedCallbacks`. This matches the "Isolation" invariant violation — one user's traffic causes another user's pending request to be denied — corresponding to a Denial of Service / cross-user response confusion bounty class, though it degrades gracefully (victim only needs to retry) rather than causing fund loss or credential exposure.

### Likelihood Explanation
Feasible in principle for the legacy handler because there is no per-sender rate limit on `HandleLegacyUserMessage` (only a TODO), and `MaxSavedCallbacks` defaults to 20000 with a 30-second prune interval, meaning an attacker must sustain roughly >10000 outstanding (unanswered) trigger requests within that window to trigger size-based eviction. This requires substantial sustained throughput and depends on DON response latency (if DON nodes answer quickly, callbacks are removed via `handleWebAPITriggerMessage` before pruning, reducing exposure) [5](#0-4) . I could not verify from the available files whether an additional upstream/network-level rate limiter (e.g., at the gateway's user HTTP server, `UserServerConfig`) throttles requests before they reach this handler — the codebase index did not surface such a limiter wired specifically to this legacy `capabilities` handler's user path. If such a limiter exists in code not covered by my search, likelihood is much lower; if not, likelihood is moderate given the required burst volume.

### Recommendation
- Track callback ownership (sender/API key/IP) alongside `createdAt` in `savedCallback`, and enforce per-sender caps or fair-eviction (e.g., evict most from the largest contributor first, or per-sender quotas) rather than pure oldest-first eviction across the whole pool.
- Add explicit per-sender rate limiting to `HandleLegacyUserMessage` (the TODO already flags this) before accepting new callbacks.
- On eviction, send an explicit error/timeout response to affected callers instead of silently dropping them, so clients can retry immediately rather than waiting for a client-side timeout.

### Proof of Concept
Go table test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with `MaxSavedCallbacks = N` (small, e.g. 10) and `CallbackMaxAgeSec` large enough that nothing expires.
2. Insert `N` "victim" `savedCallback` entries with old `createdAt` timestamps and distinct mock `Callback`s that record if `SendResponse` was ever called.
3. Insert `N` more "attacker" entries with newer `createdAt` timestamps (simulating a burst of `web_api_trigger` from one sender).
4. Call `h.pruneCallbacks()`.
5. Assert: all evicted entries are the victim (oldest) ones and none of the attacker's newer entries were evicted; assert none of the evicted victim callbacks ever got `SendResponse` invoked (confirming silent drop); assert that eviction happened purely on age/order with no per-sender protection field existing to prevent it — expected to demonstrate the vulnerability is genuine (test should currently fail an "isolation" assertion such as "no more than X% of any single non-attacker sender's callbacks were evicted").

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
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
