### Title
Unfair time-based eviction in `pruneCallbacks` allows unauthenticated flooding to evict a victim's pending legacy callback before its response arrives - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores callbacks keyed by attacker/client-chosen `MessageId` with no rate limiting or per-sender fairness (explicitly marked `TODO: apply allowlist and rate-limiting here`), and `pruneCallbacks` evicts strictly by creation time (oldest half) whenever the map exceeds `MaxSavedCallbacks`. An attacker can submit a burst of new legacy messages, causing the periodic prune to delete a victim's still-pending, legitimately older callback before the DON response arrives, silently dropping the victim's response.

### Finding Description
`HandleLegacyUserMessage` unconditionally inserts a new `savedCallback` into `h.savedCallbacks[msg.Body.MessageId]` for every incoming message that passes basic payload/timestamp/method checks, with a code comment noting no allowlist or rate-limiting is applied yet: [1](#0-0) [2](#0-1) 

The background pruning routine `pruneCallbacks`, run periodically via a ticker, first removes expired entries by age, and then — if the map still exceeds `MaxSavedCallbacks` — sorts *all* remaining entries by `createdAt` and deletes the oldest half, with no consideration of sender identity or "in-flight/legitimate" status: [3](#0-2) 

Because `MessageId` is attacker-controlled for legacy requests and there is no per-sender quota before insertion (`Start`'s prune loop only fires periodically, and no capacity check exists at insert time), an attacker can submit many messages with unique `MessageId`s in a short burst. If the victim's request was submitted slightly before the attacker's flood and has not yet received its DON response (e.g., DON nodes are slow, network delay, or intentionally many attacker requests are crafted to be "newer" than the victim's), the sort will place the victim's older, still-pending entry among the oldest half and it will be deleted by `delete(h.savedCallbacks, e.id)` at line 331 — with no notification to the victim, since the callback is simply removed from the map. When the genuine DON response later arrives via `handleWebAPITriggerMessage`, the lookup `h.savedCallbacks[msg.Body.MessageId]` will fail (`found == false`), and the response is silently dropped: [4](#0-3) 

Nothing in the reachable path (signature validation via `common.ValidatedRequestFromMessage`, timestamp freshness check, method check) prevents an attacker from submitting many distinct, validly-signed messages with unique `MessageId`s using their own keys; per-sender fairness is not implemented anywhere in this eviction path.

### Impact Explanation
This is a cross-user denial-of-response condition: an unprivileged, unauthenticated (or self-authenticated with attacker's own keys) client can cause another user's in-flight, legitimate gateway request to silently lose its DON response, with the victim's HTTP/JSON-RPC call ultimately timing out or never resolving. This matches the "request starvation / DoS affecting other users" class rather than a full node compromise, but it is a genuine cross-user isolation violation in a security-relevant queue.

### Likelihood Explanation
The only precondition is the ability to submit `MaxSavedCallbacks/2`-many validly-formed, validly-signed legacy gateway messages within the callback's pending window (bounded by `CallbackMaxAgeSec`, default 120s, and `CallbackPruneIntervalSec`, default 30s) — which requires no privileged credentials, just the ability to sign gateway messages with any key, exactly as the question's threat model describes. With default `MaxSavedCallbacks = 20000`, an attacker needs roughly 10,000 unique-`MessageId` messages within a 30-second prune window to trigger an eviction pass that could catch a victim's request created just before the flood; this is a plausible network-flood scale for an unauthenticated attacker and is fully repeatable.

### Recommendation
Add per-sender rate limiting/quota enforcement before allowing insertion into `savedCallbacks` (the `TODO` already flags this), and change `pruneCallbacks` eviction policy to be fairness-aware — e.g., cap callbacks per sender rather than a single global oldest-first eviction, or track pending vs. expired-only eviction so that only genuinely expired (age > `CallbackMaxAgeSec`) callbacks are ever removed instead of prematurely evicting non-expired ones purely to satisfy `MaxSavedCallbacks`.

### Proof of Concept
1. Construct a `handler` with `HandlerConfig{MaxSavedCallbacks: 10, CallbackMaxAgeSec: 120}` (small size for test speed), matching setup in `handler_test.go`.
2. Insert one "victim" callback directly into `h.savedCallbacks` with `createdAt = now` via a call to `HandleLegacyUserMessage` (or direct map insertion) using a distinguishable `MessageId` and a mock `handlers.Callback` that records whether `SendResponse` is called.
3. Immediately insert `MaxSavedCallbacks` additional "attacker" callbacks with unique `MessageId`s and `createdAt = now.Add(time.Millisecond)` (i.e., slightly newer than the victim's), simulating a rapid flood, exceeding `MaxSavedCallbacks`.
4. Call `h.pruneCallbacks()` directly.
5. Assert that the victim's `MessageId` entry has been deleted from `h.savedCallbacks` (`_, found := h.savedCallbacks[victimID]; assert.False(t, found)`).
6. Then simulate the victim's DON response arriving via `handleWebAPITriggerMessage` with the victim's `MessageId` and assert that the mock victim callback's `SendResponse` was never invoked, demonstrating the silently dropped response.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-334)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

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
