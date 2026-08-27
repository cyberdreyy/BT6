### Title
Unauthenticated flooding of `savedCallbacks` in the gateway's capabilities handler enables victim-callback eviction (DoS) - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` stores every incoming `web_api_trigger` request in the shared `savedCallbacks` map keyed by attacker-controlled `MessageId`, with no allowlist or per-sender quota (explicitly marked `// TODO: apply allowlist and rate-limiting here`). Since `pruneCallbacks` evicts the oldest half of all entries purely by `createdAt` once `MaxSavedCallbacks` (default 20000) is exceeded, an attacker who can send enough signed requests can force eviction of a legitimate victim's still-pending callback, causing the victim to never receive a response.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) validates payload shape, timestamp freshness, and method name, then unconditionally inserts the callback into `h.savedCallbacks[msg.Body.MessageId]` [1](#0-0) . The code explicitly notes the missing control: `// TODO: apply allowlist and rate-limiting here` [2](#0-1) . There is no per-user/per-sender cap on how many entries a single caller can occupy in the map — only a global size limit is enforced later by the pruning goroutine.

`pruneCallbacks` (core/services/gateway/handlers/capabilities/handler.go:299-339) runs periodically (every `CallbackPruneIntervalSec`, default 30s) and, after removing entries older than `CallbackMaxAgeSec` (default 120s), if the map still exceeds `MaxSavedCallbacks` it sorts remaining entries by `createdAt` and deletes the oldest half, entirely independent of whether the caller was a legitimate DON-serving request or an attacker flood [3](#0-2) . This eviction is oblivious to "who owns" a callback — it only orders by insertion time, so a flood of attacker requests inserted just after a victim's request will not evict the victim (since the victim's is older), but a flood of attacker requests inserted *before* a victim's request (or sustained continuously such that the map perpetually exceeds `MaxSavedCallbacks`) will push the victim's callback into the "oldest half" and delete it before the corresponding DON node response arrives via `handleWebAPITriggerMessage` (core/services/gateway/handlers/capabilities/handler.go:148-162), which does a map lookup by `MessageId` and silently no-ops if not found (`found` is false, function returns nil without notifying anyone).

Because `MessageId` values are attacker-supplied within the signed message body and the handler performs no uniqueness/ownership check against other keys derived from sender identity, sustained flooding by unprivileged callers (any address capable of sending signed gateway `HandleLegacyUserMessage` requests) is sufficient to keep the map oversized and to bias eviction against victims whose requests are in flight during a flood window.

### Impact Explanation
This is a Denial-of-Service against arbitrary gateway users: a victim's pending web API trigger callback can be silently dropped, so `callback.SendResponse` is never called and the caller-facing HTTP/GraphQL request (whatever route ultimately awaits this callback) will hang or time out without ever getting the legitimate response. This matches a "denial of service against another user's request" impact class rather than fund loss or credential exposure. The claim about `MessageId` reuse producing cross-user delivery of a stale response requires the attacker to guess/collide a victim's exact `MessageId` after eviction, which is not demonstrated as feasible here (no evidence `MessageId`s are predictable or attacker-influenced to collide with a specific victim's), so that secondary claim is unconfirmed and should be treated as unproven.

### Likelihood Explanation
Preconditions: attacker needs the ability to send signed `HandleLegacyUserMessage` / `web_api_trigger` requests to the gateway. The code and comments confirm no allowlist or rate limiting currently gates this call path [2](#0-1) ; only a downstream, unrelated `nodeRateLimiter` exists, gating outgoing node messages (`handleWebAPIOutgoingMessage`), not incoming user messages [4](#0-3) . I was not able to trace the full HTTP-to-`HandleLegacyUserMessage` route/signature-verification path within available tool iterations (e.g., whether upstream connection-manager code enforces any authentication or per-key request quota before reaching this handler), so likelihood is bounded by that unresolved dependency — if an upstream layer already enforces strict per-sender authentication and quota, exploitability is reduced. Given the explicit TODO in this exact function, the repository authors themselves acknowledge the gap remains open at this handler layer.

### Recommendation
- Add per-sender (public key / node address) quotas on `savedCallbacks` insertion in `HandleLegacyUserMessage`, rejecting or rate-limiting when a single sender holds too many pending entries.
- When evicting in `pruneCallbacks`, prefer age-based (already-expired) eviction only, and avoid blanket "oldest half" eviction that can remove not-yet-expired legitimate entries; alternatively, evict oldest-per-sender rather than globally oldest to bound any one sender's impact.
- Enforce the allowlist noted in the TODO at core/services/gateway/handlers/capabilities/handler.go:384 before accepting/storing a callback.
- Ensure `handleWebAPITriggerMessage`'s "callback not found" path surfaces an internal metric/alert so silently dropped responses are observable.

### Proof of Concept
Go unit test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with `MaxSavedCallbacks` set to a small number (e.g., 10) for test speed, `CallbackMaxAgeSec` large enough that nothing expires by age.
2. Register one victim callback via `HandleLegacyUserMessage` with a distinguishable `MessageId` ("victim-1") and a mock `handlers.Callback` whose `SendResponse` sets a flag/records the call.
3. Immediately flood the handler with `MaxSavedCallbacks` (or more) attacker `HandleLegacyUserMessage` calls, each with unique `MessageId`s, submitted with `createdAt` timestamps interleaved to be inserted around/after the victim's entry (or call `pruneCallbacks()` directly while the map exceeds `MaxSavedCallbacks`).
4. Call `h.pruneCallbacks()` and then assert `h.savedCallbacks["victim-1"]` no longer exists even though it has not exceeded `CallbackMaxAgeSec`.
5. Simulate the DON response for "victim-1" via `handleWebAPITriggerMessage` and assert the victim's mock `SendResponse` is never invoked (proving the response is silently dropped) — demonstrating the DoS.
6. (Optional, to test the MessageId-reuse claim) After eviction, insert a new legitimate callback reusing "victim-1" as `MessageId` and confirm whether a stale/racing node response could be delivered to the wrong callback; if no collision path is found in code, mark that part of the hypothesis unconfirmed.

### Citations

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
