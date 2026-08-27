### Title
Unbounded, unrate-limited `savedCallbacks` map allows attacker to evict legitimate in-flight callbacks via `pruneCallbacks` eviction, causing cross-user Denial of Service - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` inserts an entry into the shared `h.savedCallbacks` map for every valid, correctly-signed legacy trigger message it receives, with **no allowlist or per-sender rate limiting** (explicitly marked with a `// TODO: apply allowlist and rate-limiting here` comment). Because the map is a single shared resource for all users of the DON handler, and the background `pruneCallbacks` reaper evicts the *oldest* entries whenever the map exceeds `MaxSavedCallbacks`, an attacker capable of sending many distinct signed trigger messages can force eviction of another user's still-pending, legitimate callback before its DON response arrives.

### Finding Description
- `HandleLegacyUserMessage` (handler.go, lines 341–421) validates only the message signature/shape (`common.ValidatedRequestFromMessage`) and basic freshness (`payload.Timestamp` staleness check), then unconditionally stores the callback keyed by `msg.Body.MessageId`: [1](#0-0) 
- The code explicitly acknowledges the missing control: [2](#0-1) 
- Periodically, `pruneCallbacks` (lines 299–339) first removes expired entries, then — if the map still exceeds `MaxSavedCallbacks` (`maxSize`) — sorts all remaining entries ascending by `createdAt` and deletes everything except the newest `maxSize/2`: [3](#0-2) 
- `MessageId` is attacker-controlled/disposable per request, and there is no per-sender quota tying insertions to a specific identity/allowlist entry, so any attacker able to produce valid signed gateway messages (an accepted low-privilege attacker profile) can create an unbounded number of distinct `savedCallback` entries.
- Because `savedCallbacks` is a single global map shared across all requesters routed through this handler/DON, once the attacker floods enough entries to push the map size above `MaxSavedCallbacks` (default `defaultMaxSavedCallbacks = 20000`), the next prune cycle deletes the oldest half of all entries — which necessarily includes any legitimate victim requests that were submitted earlier than the bulk of the attacker's flood, even though those victim callbacks are still awaiting a DON response.
- When a legitimate victim's `savedCallback` is deleted, the eventual DON response in `handleWebAPITriggerMessage` (lines 148–162) finds `found == false` (because the map entry no longer exists) and silently drops the response — the victim's request permanently times out with no DON answer delivered, i.e., denial of service against another user's in-flight request.
- Nothing in the checked code stops this: signature validation only proves the message is a validly formed legacy trigger, it does not gate whether that sender is allowed to consume space in the shared callback map, and there is no allowlist or rate limiter applied on this path (the only rate limiter, `nodeRateLimiter`, is applied to a different method — outgoing node messages — not to incoming user messages in `HandleLegacyUserMessage`).

### Impact Explanation
This is a resource-exhaustion / quota-bypass style Denial of Service: an unprivileged attacker able to submit signed gateway requests can force the gateway to drop other users' legitimate, in-flight DON responses, denying them service. This matches the "unauthorized action" / availability-impact bounty class for gateway/DON request handling — legitimate users' subscriptions/callbacks are starved due to shared, unbounded, unthrottled state controlled by an unrelated attacker.

### Likelihood Explanation
- Minimal precondition: the attacker only needs the ability to produce validly-signed gateway messages (the accepted unprivileged attacker profile explicitly includes "any address sending signed gateway requests").
- No allowlist or per-sender rate limit currently gates entry into `savedCallbacks` (confirmed by the in-code TODO comment).
- The attack is fully repeatable and requires only volume (message count), not any special timing beyond exceeding `MaxSavedCallbacks` before the next prune cycle, which runs periodically (`CallbackPruneIntervalSec`, default 30s).
- Feasibility is high given each request is otherwise cheap (only requires a valid signature and non-stale timestamp).

### Recommendation
- Apply per-sender rate limiting and/or an allowlist check in `HandleLegacyUserMessage` before inserting into `savedCallbacks`, as the existing TODO comment already flags.
- Consider bounding `savedCallbacks` per-sender (e.g., a max number of pending callbacks per sender address) rather than only a single global cap, so one sender cannot starve others.
- Optionally reject new insertions (with a "too many pending requests" error response) instead of silently evicting older, still-valid entries when the global cap is reached.

### Proof of Concept
Go unit test plan (in `handler_test.go` alongside existing `pruneCallbacks` tests):
1. Construct a `handler` with `config.MaxSavedCallbacks` set to a small value, e.g. `10`.
2. Insert one `savedCallback` representing "victim" with `createdAt = now` and a callback whose `SendResponse` is tracked via a mock/spy to detect if it's ever invoked.
3. Insert `N > MaxSavedCallbacks` additional `savedCallback` entries (simulating attacker flood) each with distinct `MessageId`s and `createdAt` timestamps strictly after the victim's, using a mock callback that just records calls.
4. Call `h.pruneCallbacks()`.
5. Assert:
   - `len(h.savedCallbacks) == MaxSavedCallbacks/2`.
   - The victim's `MessageId` is no longer present in `h.savedCallbacks` (evicted because it was the oldest entry).
   - Simulate the DON response for the victim's original `MessageId` via `handleWebAPITriggerMessage`; assert the victim's `SendResponse` mock is never called (response silently dropped), demonstrating the victim never receives its DON response due to the attacker-induced eviction.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L410-414)
```go

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
