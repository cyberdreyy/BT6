### Title
Unbounded growth of the per-sender rate limiter map allows memory-exhaustion DoS - ([File: core/services/workflows/ratelimiter/ratelimiter.go])

### Summary
`RateLimiter.Allow` lazily creates and permanently stores a new `*rate.Limiter` for every distinct `sender` key it observes, with no eviction, expiry, or maximum-size bound, analogous to the `HoldefiSettings.marketsList` array that grows endlessly because entries are only ever added, never removed. Since sender identifiers are attacker-influenceable (workflow owner/sender addresses derived from on-chain events processed by the workflow registry syncer), an unprivileged actor can force the map to grow without bound, exhausting node memory.

### Finding Description
`RateLimiter` wraps a global `rate.Limiter` plus a `perSender map[string]*rate.Limiter`: [1](#0-0) 

`Allow` inserts a brand-new `rate.Limiter` into `perSender` the first time a given sender string is seen, and the map is never pruned: [2](#0-1) 

This limiter is constructed with a `PerSenderRPS`/`PerSenderBurst` config and used by the workflow registry syncer (`core/services/workflows/syncer/v2/helpers.go`) where the "sender" key is derived from workflow owner addresses observed via on-chain events, which are cheap for any external party to generate: [3](#0-2) 

Unlike `core/services/gateway/handlers/capabilities/handler.go`'s `savedCallbacks` map — which has an explicit `pruneCallbacks` routine bounding it by both age and `MaxSavedCallbacks` — the `ratelimiter.RateLimiter.perSender` map has no analogous size cap or eviction path, so it can grow unboundedly as long as new sender identifiers keep appearing, exactly mirroring the "list of markets can endlessly grow" root cause (append-only, no-removal data structure keyed by attacker-influenceable input).

### Impact Explanation
Each new distinct sender value permanently allocates a `rate.Limiter` object that is never freed for the lifetime of the process. An attacker who can cause many distinct sender identities to be processed (e.g., registering many workflow owner addresses on the registry, which is cheap since address generation is free and only requires paying for on-chain transactions) can grow this map indefinitely, leading to unbounded memory growth and eventual node instability/crash — a denial-of-service condition affecting the availability of workflow processing for the whole node, not just the attacker.

### Likelihood Explanation
Likelihood is limited by the fact that reaching this code path requires driving on-chain workflow registry events with new sender/owner addresses (which costs gas but not permission), rather than a pure zero-cost unauthenticated HTTP/gateway request. This is a lower-likelihood, higher-cost DoS vector compared to a directly internet-facing unauthenticated endpoint, but it is still reachable by any unprivileged blockchain participant without special roles.

### Recommendation
Bound the `perSender` map, e.g., by:
- Enforcing a maximum number of tracked senders and evicting least-recently-used entries when exceeded (similar to the `pruneCallbacks`/`MaxSavedCallbacks` pattern already used in `core/services/gateway/handlers/capabilities/handler.go`).
- Adding a TTL-based sweep goroutine that removes `rate.Limiter` entries for senders that have been idle beyond a configurable window.
- Alternatively, replacing the per-sender map with a fixed-capacity LRU cache of limiters.

### Proof of Concept
Not applicable — this is a design-level unbounded-growth issue rather than an exploit requiring a specific payload; the code excerpts above are sufficient to demonstrate the missing eviction logic (`core/services/workflows/ratelimiter/ratelimiter.go:40-52`).

### Citations

**File:** core/services/workflows/ratelimiter/ratelimiter.go (L10-23)
```go
// Wrapper around Go's rate.Limiter that supports both global and a per-sender rate limiting.
type RateLimiter struct {
	global    *rate.Limiter
	perSender map[string]*rate.Limiter
	config    Config
	mu        sync.Mutex
}

type Config struct {
	GlobalRPS      float64 `json:"globalRPS"`
	GlobalBurst    int     `json:"globalBurst"`
	PerSenderRPS   float64 `json:"perSenderRPS"`
	PerSenderBurst int     `json:"perSenderBurst"`
}
```

**File:** core/services/workflows/ratelimiter/ratelimiter.go (L40-52)
```go
func (rl *RateLimiter) Allow(sender string) (senderAllow bool, globalAllow bool) {
	rl.mu.Lock()
	senderLimiter, ok := rl.perSender[sender]
	if !ok {
		senderLimiter = rate.NewLimiter(rate.Limit(rl.config.PerSenderRPS), rl.config.PerSenderBurst)
		rl.perSender[sender] = senderLimiter
	}
	rl.mu.Unlock()

	senderAllow = senderLimiter.Allow()
	globalAllow = rl.global.Allow()
	return senderAllow, globalAllow
}
```

**File:** core/services/workflows/syncer/v2/helpers.go (L19-24)
```go
var rlConfig = ratelimiter.Config{
	GlobalRPS:      1000.0,
	GlobalBurst:    1000,
	PerSenderRPS:   30.0,
	PerSenderBurst: 30,
}
```
