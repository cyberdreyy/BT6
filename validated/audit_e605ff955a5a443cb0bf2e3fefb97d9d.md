### Title
Shared `RequestCache` in gateway handlers has no per-sender quota, allowing an unauthenticated flood of distinct sender/MessageId pairs to exhaust `maxCacheSize` and deny service to legitimate senders - ([File: core/services/gateway/handlers/common/requestcache.go])

### Summary
`requestCache.NewRequest` keys pending requests by `globalId{sender, id}` and only rejects new requests once the *global* map size reaches `maxCacheSize`, with no per-sender bound at this layer. An attacker who can submit many distinct signed messages (e.g., generated ECDSA keys with distinct sender addresses and message IDs) can fill the shared cache, causing subsequent legitimate senders' `NewRequest` calls to fail with `"request cache is full"` until entries expire via timeout.

### Finding Description
`NewRequest` (core/services/gateway/handlers/common/requestcache.go:50-76) builds a `globalId{request.Body.Sender, request.Body.MessageId}` key [1](#0-0) , checks for an existing duplicate key, and only then checks `len(c.cache) >= int(c.maxCacheSize)` [2](#0-1) . There is no per-sender counter or quota anywhere in this type — `requestCache` only tracks a flat `map[globalId]*pendingRequest[T]` and a single global `maxCacheSize` [3](#0-2) . Since the key includes the sender, an attacker generating many distinct sender addresses (trivial with ECDSA key generation) combined with distinct MessageIds can create `maxCacheSize` distinct cache entries and consume the entire shared capacity, causing a subsequent legitimate sender's call to fail with `"request cache is full"`.

However, this component alone does not constitute a full exploit path independent of context: `RequestCache` is a low-level shared building block used by gateway handlers (e.g., `confidentialrelay` handler), and its doc/tests explicitly note this is generic infrastructure. Whether an unauthenticated attacker can actually reach `NewRequest` with unlimited distinct senders depends on the calling handler's own authentication/rate-limiting layer. In the handlers actually inspected (e.g., HTTP trigger handler, vault gateway handler), there IS a dedicated per-sender/per-workflow-owner rate limiter enforced *before* reaching request caching/dispatch logic [4](#0-3) , and the README for that handler documents "Per-Sender Limits: Individual rate limits per sending entity" and "Global Limits: System-wide rate limiting for overall protection" as an explicit security feature [5](#0-4) . This confirms the per-sender quota is intentionally enforced one layer up (rate limiter), not inside `RequestCache` itself, consistent with the design comment implied by the audit question.

I could not find, within the available index, a caller of `RequestCache.NewRequest` that omits per-sender rate limiting entirely before reaching the cache — the `confidentialrelay` handler test shows duplicate-ID rejection and node-level rate limiting but I was unable to fully verify whether every consumer of `RequestCache` in the codebase applies a per-sender rate limiter ahead of `NewRequest`. This is a gap in my verification, not a confirmed missing control.

### Impact Explanation
If a caller of `RequestCache` lacks its own per-sender rate limiting/quota, this shared-cache design allows a resource-exhaustion (DoS) condition where an unprivileged attacker can flood the cache with `maxCacheSize` distinct entries, blocking new requests from any other sender until the flooding entries time out (`timeout` duration) or are resolved. This matches a low-severity availability/DoS impact class, not a secret-disclosure, auth-bypass, or fund-movement impact — no cross-user data confusion or key material exposure occurs here.

### Likelihood Explanation
Exploitability is contingent on the specific handler wiring `RequestCache` into its request pipeline without an independent per-sender rate limiter ahead of it. Where such a limiter exists (confirmed for the CRE HTTP handler v2 path), the attack is mitigated at that layer before `NewRequest` is ever reached with attacker-controlled volume. I was unable to confirm from the available index whether any currently-deployed handler invokes `RequestCache.NewRequest` without a preceding per-sender limiter — the `requestcache.go` file itself provides no protection, but this alone is expected/by-design "generic cache" behavior, with quota enforcement delegated to callers, as suggested by the audit question's own proof idea ("should be enforced by rate limiting outside this file").

### Recommendation
Given `RequestCache` is intended as reusable infrastructure with no built-in authentication context, the safest fix is defense-in-depth: add an optional per-sender cap parameter to `requestCache` (e.g., track counts per `sender` in a secondary map and reject `NewRequest` once a sender's count exceeds a configurable threshold, independent of the global `maxCacheSize`), rather than relying solely on external rate limiting layers that may not be present or configured for every consumer of this type.

### Proof of Concept
Not producing a full standalone PoC as a confirmed vulnerability, because validation requires confirming absence of a per-sender rate limiter in the specific handler under audit, which I could not fully verify. A minimal repeatable unit test to demonstrate the underlying cache behavior (already partially present as `TestRequestCache_MaxSize` in requestcache_test.go) would be:
1. Create `NewRequestCache[T](timeout, maxCacheSize=N)`.
2. Loop `N` times, each iteration generating a distinct `sender` and `MessageId`, calling `NewRequest` and asserting `nil` error.
3. Call `NewRequest` once more with a new distinct sender/id pair and assert the error is `"request cache is full"`.
4. This confirms no per-sender partitioning exists at the `RequestCache` layer — but to prove a security-relevant DoS, the PoC must additionally show a concrete handler path lacking upstream rate limiting, which was not established here.

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L27-32)
```go
type requestCache[T any] struct {
	cache        map[globalId]*pendingRequest[T]
	maxCacheSize uint32
	timeout      time.Duration
	mu           sync.Mutex
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L57-57)
```go
	key := globalId{request.Body.Sender, request.Body.MessageId}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L60-66)
```go
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L120-151)
```go
func NewGatewayHandler(handlerConfig json.RawMessage, shardedDONs []config.ShardedDONConfig, shardsConnMgrs [][]handlers.DON, httpClient network.HTTPClient, lggr logger.Logger, lf limits.Factory, httpClientFactory network.HTTPClientFactory) (*gatewayHandler, error) {
	var cfg ServiceConfig
	err := json.Unmarshal(handlerConfig, &cfg)
	if err != nil {
		return nil, err
	}
	cfg = WithDefaults(cfg)

	shards, nodeAddrToShard, err := buildShardEndpoints(shardedDONs, shardsConnMgrs)
	if err != nil {
		return nil, fmt.Errorf("failed to build shard endpoints: %w", err)
	}
	members := allMembers(shards)

	globalNodeRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.GatewayHTTPGlobalRate)
	if err != nil {
		return nil, fmt.Errorf("failed to create global node rate limiter: %w", err)
	}
	perNodeRateLimiters := make(map[string]limits.RateLimiter, len(members))
	for _, member := range members {
		var rl limits.RateLimiter
		rl, err = lf.MakeRateLimiter(cresettings.Default.GatewayHTTPPerNodeRate)
		if err != nil {
			return nil, fmt.Errorf("failed to create per-node rate limiter for %s: %w", member.Address, err)
		}
		perNodeRateLimiters[member.Address] = rl
	}

	userRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create user rate limiter: %w", err)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L211-216)
```markdown
### 7.2 Rate Limiting

- **Dual Rate Limiting**: Separate limits for node and user requests
- **Per-Sender Limits**: Individual rate limits per sending entity
- **Global Limits**: System-wide rate limiting for overall protection

```
