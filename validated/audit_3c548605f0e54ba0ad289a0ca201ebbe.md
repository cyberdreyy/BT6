### Title
Shared gateway RequestCache can be griefed to capacity, denying service to legitimate node/workflow requests - (File: core/services/gateway/handlers/common/requestcache.go)

### Summary
The C4 finding describes a griefing pattern: a shared/limited resource is gated by a check (`investedAssets() == 0`) that any unprivileged party can keep non-zero by sending trivial amounts, permanently blocking a legitimate operation (`setStrategy`) until the operator repeatedly redeems and the griefer repeats the attack. The closest reachable analog in this codebase is the gateway's generic `RequestCache`, a shared, capacity-limited map used by internet-facing gateway handlers to track pending user/node requests. Its capacity check is a simple size comparison with no per-sender quota, so an unprivileged, un-authenticated caller can keep the shared resource saturated indefinitely, denying legitimate requests — structurally the same "unprivileged actor can keep a shared gate non-empty/blocked" pattern as the vault bug.

### Finding Description
`requestCache.NewRequest` keys pending entries by `globalId{sender, messageId}` [1](#0-0)  but enforces capacity against the *global* map size, not per-sender:

```go
if len(c.cache) >= int(c.maxCacheSize) {
    return errors.New("request cache is full")
}
``` [2](#0-1) 

Because `sender` is attacker-controlled input from the message envelope, and `messageId` can be freely varied, a single unprivileged sender can create up to `maxCacheSize` distinct pending entries. Each entry only frees up after its own timeout elapses (`c.timeout`) or a matching response arrives [3](#0-2) . As long as the attacker keeps submitting new `messageId`s faster than the timeout reaps old ones, `len(c.cache)` stays at capacity, and every other legitimate request — from any other sender — is rejected with `"request cache is full"`. This mirrors the vault's `investedAssets() == 0` gate: a shared precondition/finite resource that an unprivileged actor can keep permanently occupied with trivial, repeatable input, blocking the intended function for everyone else.

Unlike the vault bug (which blocks an admin-privileged action), this analog blocks a *user-facing* capability (pending-request tracking) at the internet-facing gateway boundary, which is explicitly in scope per the analog rules (message envelopes/handlers/caches).

### Impact Explanation
If `maxCacheSize` is fixed and shared across all callers of a given gateway handler instance, an attacker with no special privileges can fill the cache with garbage requests, causing legitimate node/workflow requests to be dropped (`"request cache is full"`). This is a denial-of-service on the affected gateway capability, degrading availability for all tenants sharing that handler instance — a direct parallel to "griefers gonna grief" from the original report, since the attack is cheap, repeatable, and requires no elevated access.

### Likelihood Explanation
Likelihood is moderate to high wherever `RequestCache` is used behind an endpoint reachable by external/unprivileged senders with attacker-controlled `sender`/`messageId` fields, since the only cost to the attacker is generating new message IDs, and the fix (waiting for `c.timeout` reaping) requires either time or admin intervention, same as the vault case requiring redemption cycles. Actual severity depends on which concrete handler(s) instantiate `RequestCache` with what `maxCacheSize`/`timeout` values and whether sender identity is otherwise authenticated/quota-limited before requests reach this cache — I was not able to fully enumerate all call sites and their upstream authentication within the available context, so this should be verified against the specific handler wiring (e.g., per-sender vs. global cache instantiation) before treating it as confirmed exploitable in production.

### Recommendation
- Enforce a per-sender quota (not just a global cap) on entries in `RequestCache`, e.g., track and limit `len(entries for sender)` in addition to global size.
- Consider evicting/prioritizing by sender fairness (e.g., round-robin or per-sender sub-caches) so one sender cannot starve others.
- Reduce the timeout window or require senders to be authenticated/rate-limited upstream before entries are admitted into the cache, mirroring the "keep an internal balance and gate on the tracked figure, not raw controllable state" mitigation from the original report — here, gate acceptance on a per-identity tracked count instead of only the raw global map length.

### Proof of Concept
1. An unprivileged client sends gateway requests with `sender = "attacker"` and unique `messageId` values (`id-1`, `id-2`, ..., `id-N`) where `N == maxCacheSize`, each routed to a handler using `RequestCache.NewRequest`.
2. Each call succeeds because `globalId{sender, id}` is unique and `len(c.cache) < maxCacheSize` up to the Nth call [4](#0-3) .
3. Once `len(c.cache) == maxCacheSize`, all subsequent `NewRequest` calls from any sender fail with `"request cache is full"` [5](#0-4)  until the attacker's entries time out via `c.timeout` [6](#0-5) .
4. The attacker repeats step 1 just before entries expire, keeping the shared cache perpetually saturated and denying service to legitimate senders — the same repeatable-griefing dynamic as the original vault finding.

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L34-37)
```go
type globalId struct {
	sender string
	id     string
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L46-76)
```go
func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```
