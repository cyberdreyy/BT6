### Title
Unbounded `savedCallbacks` growth in `HandleLegacyUserMessage` lets an unauthenticated flood evict legitimate pending user callbacks before DON responses arrive - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` inserts every incoming user message into `h.savedCallbacks` with no quota or rate-limiting check, as explicitly noted by the `TODO: apply allowlist and rate-limiting here` comment. Because eviction is only performed asynchronously by `pruneCallbacks` on a fixed timer, and it deletes the *oldest* entries first, a high-volume unauthenticated flood can force the periodic prune to evict a legitimate, still-pending victim callback that was registered before the flood, causing that victim to silently never receive `SendResponse`.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) validates payload shape/timestamp/method/signature format, but performs no per-caller rate limiting or allowlist check on the number of concurrently tracked callbacks — the comment at line 384 (`// TODO: apply allowlist and rate-limiting here`) confirms this gap is a known unaddressed spot. Every valid message unconditionally does: [1](#0-0) 
adding an entry to the `h.savedCallbacks` map keyed by `msg.Body.MessageId`, with no check against `h.config.MaxSavedCallbacks` at insertion time.

Eviction only happens out-of-band in `pruneCallbacks`, invoked once every `CallbackPruneIntervalSec` (default 30s) by a background goroutine started in `Start`: [2](#0-1) 

When the map size exceeds `MaxSavedCallbacks`, `pruneCallbacks` sorts entries by `createdAt` ascending and deletes all but the newest `maxSize/2`: [3](#0-2) 

This means the *oldest* callbacks — which are precisely the legitimate, longest-pending requests waiting for a DON response — are the first to be evicted, while a flood of newer attacker-generated messages (created just before the prune tick) survive. There is no per-caller/per-address quota, so a single attacker sending enough `HandleLegacyUserMessage` requests within one prune interval can push the map size far past `MaxSavedCallbacks` (the code comment at line 44, `defaultMaxSavedCallbacks = 20000 // could briefly exceed under heavy load`, acknowledges unbounded growth between prunes). The victim's entry is deleted from `h.savedCallbacks` and, since `handleWebAPITriggerMessage` only calls `savedCb.SendResponse` if the entry is still `found` in the map, the victim's `SendResponse` callback is never invoked and their HTTP call to the gateway will hang until its own timeout/context expiry, receiving no proper response — a silent denial of service against another user's request caused by an unauthenticated flood.

### Impact Explanation
This is a Denial-of-Service / cross-user isolation violation: an unauthenticated, unprivileged caller can cause another user's legitimate, in-flight gateway request to be silently dropped (no error, no DON response delivered) purely by volume, without needing any credentials, allowlist membership, or access to the target DON. This matches the Chainlink bounty impact class of "unauthorized denial of service affecting another user's request/response flow" via a gateway-facing, unauthenticated endpoint.

### Likelihood Explanation
- Preconditions: none beyond network access to the gateway's legacy user message endpoint; the endpoint requires only a syntactically valid signed message (any self-generated signing key works, per `ValidatedRequestFromMessage`), not membership in any allowlist.
- The attack is trivially repeatable and requires only enough concurrent requests to push `len(h.savedCallbacks)` above `MaxSavedCallbacks` before the next prune tick (up to `CallbackPruneIntervalSec`, default 30s, giving ample time budget).
- No rate limiting exists on the user-message ingress path (confirmed by the explicit TODO), so this is fully feasible for a single unprivileged actor with modest request throughput.

### Recommendation
- Enforce the `MaxSavedCallbacks` limit synchronously at insertion time in `HandleLegacyUserMessage`, rejecting or rate-limiting new registrations once the limit is reached, rather than relying solely on the periodic `pruneCallbacks` sweep.
- Add per-caller/per-source rate limiting and/or an allowlist check on the legacy user message path, as flagged by the existing TODO.
- Consider using an LRU/quota structure keyed by requester identity so one caller cannot consume the entire shared `savedCallbacks` capacity and force eviction of other users' entries.

### Proof of Concept
Handler-level integration test plan:
1. Construct a `handler` via `NewHandler` with a small `MaxSavedCallbacks` (e.g., 10) and `CallbackPruneIntervalSec` set low for test speed.
2. Register one legitimate callback: call `HandleLegacyUserMessage` with a valid signed message `victimMsg`, using a `Callback` mock whose `SendResponse` is tracked (e.g., via a channel/flag), and record `t0 = time.Now()`.
3. Immediately after, concurrently issue `MaxSavedCallbacks * 2` additional `HandleLegacyUserMessage` calls (from distinct signing keys, simulating different unauthenticated flood sources) with distinct `MessageId`s and no intention of ever receiving a real DON response.
4. Manually invoke `h.pruneCallbacks()` (or wait for the ticker) once the flood completes.
5. Assert: `victimMsg`'s entry is no longer present in `h.savedCallbacks`, and the victim's mock `Callback.SendResponse` was never invoked even after simulating a subsequent DON response for `victimMsg`'s `MessageId` via `HandleNodeMessage`/`handleWebAPITriggerMessage` — demonstrating the victim's legitimate response is permanently lost due to the flood-induced eviction.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L269-285)
```go
func (h *handler) Start(context.Context) error {
	return h.StartOnce(handlerName, func() error {
		h.wg.Go(func() {
			ticker := time.NewTicker(time.Duration(h.config.CallbackPruneIntervalSec) * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					h.pruneCallbacks()
				case <-h.stopCh:
					return
				}
			}
		})
		return nil
	})
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
