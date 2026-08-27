### Title
Unauthenticated `web_api_trigger` flood evicts other users' pending callbacks via `pruneCallbacks` age-agnostic oldest-half trim - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores every incoming trigger request in the shared `h.savedCallbacks` map keyed only by `msg.Body.MessageId`, with no authentication, allowlist, or per-sender rate limiting applied before insertion (the code even has a `// TODO: apply allowlist and rate-limiting here` comment). `pruneCallbacks` later evicts the oldest half of all entries once `MaxSavedCallbacks` is exceeded, without regard to which user/sender owns each entry, so an unauthenticated flood can force eviction of a victim's still-valid, unexpired callback.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) validates only payload well-formedness, non-zero timestamp, message staleness, and method name — it performs no caller authentication/allowlist/rate-limit check (confirmed by the explicit TODO at line 384 and the corresponding test-file TODO in `handler_test.go`: "Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated"). Any request that passes these superficial checks is inserted into `h.savedCallbacks[msg.Body.MessageId]` (line 412), which is a single global map shared across all callers of the DON's `web_api_trigger` method.

`pruneCallbacks` (lines 299-339) first removes entries older than `CallbackMaxAgeSec`, then, if the map still exceeds `MaxSavedCallbacks`, sorts **all remaining entries globally by `createdAt`** and deletes `entries[:len(entries)-maxSize/2]` — i.e., the oldest half of *all* users' pending callbacks, with no per-sender/per-key partitioning or ownership check. Because entry age is attacker-influenceable timing (an attacker just needs to submit requests after the victim's), an attacker who submits `MaxSavedCallbacks` (default 20000, `defaultMaxSavedCallbacks`) or more requests in a burst can guarantee that older legitimate entries — potentially the victim's still-within-`CallbackMaxAgeSec` callback — fall into the oldest half and are silently deleted, with no response ever sent to that victim's blocked callback.

### Impact Explanation
This breaks the isolation invariant between unrelated users of the gateway's web API trigger relay: an unauthenticated flood from one user can cause another legitimate, still-pending user's long-poll/WS HTTP request to hang indefinitely (their callback's `SendResponse` is never invoked because the entry is deleted from `savedCallbacks` before a node response arrives). This is a Denial-of-Service on request/response delivery guarantees rather than a direct authentication/authorization bypass or fund/secret compromise, matching a "denial of service impacting legitimate node/user operation" class rather than a critical/high fund-loss class.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only unauthenticated ability to submit `web_api_trigger` messages fast enough to exceed `MaxSavedCallbacks` (20000 by default) before the periodic `pruneCallbacks` (default every `CallbackPruneIntervalSec`=30s) runs, or sustain a rate that keeps the map oversized across prune cycles. No credentials, roles, or special access are required beyond whatever minimal message construction (signing per `common.ValidatedRequestFromMessage`) is needed to pass `ValidatedRequestFromMessage`; this depends on the message-signing/authentication scheme applied earlier in the gateway pipeline, which was not fully traced in the available index (I could not locate the exact HTTP entrypoint / connector code that calls `HandleLegacyUserMessage` to confirm whether any upstream per-sender authentication or throttling exists before this handler is reached). Given the explicit TODO in the handler itself acknowledging missing allowlist/rate-limiting at this layer, the flood is plausible without additional privilege.

### Recommendation
- Partition `savedCallbacks` per sender/key or enforce per-sender quotas so one sender's flood cannot count against another sender's slot budget.
- Add the sender-based allowlist/rate-limiting referenced by the existing TODO comment before inserting into `savedCallbacks`.
- When evicting due to `MaxSavedCallbacks`, prefer per-sender fairness (e.g., cap entries per sender, or evict only entries from senders exceeding quota) instead of a single global oldest-half sort.
- Alternatively, reject new insertions once the map is at capacity rather than evicting existing valid, unexpired entries.

### Proof of Concept
Go unit test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Build a `handler` with `MaxSavedCallbacks` set to a small test value (e.g. 10) via `HandlerConfig`.
2. Seed `handler.savedCallbacks` with one "victim" entry keyed `"victim-msg"` with `createdAt = time.Now()` and a callback whose `SendResponse` is tracked (e.g. via a channel/mock).
3. Call `handler.HandleLegacyUserMessage` `MaxSavedCallbacks` additional times with distinct valid signed messages/message IDs (`attacker-0`..`attacker-N`), each with slightly later `createdAt` than the victim's, simulating an unauthenticated flood.
4. Manually invoke `handler.pruneCallbacks()`.
5. Assert that `handler.savedCallbacks["victim-msg"]` no longer exists (evicted) even though `time.Since(victim.createdAt) < CallbackMaxAgeSec`, and that the victim's callback's `SendResponse`/`Wait` never resolves — demonstrating a legitimate user's still-valid pending request was silently dropped due to another user's flood. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
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

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
