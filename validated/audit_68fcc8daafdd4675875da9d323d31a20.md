## Analysis

The Lighthouse fix consolidated `Observed*` "seen cache" structures so that the *check* and *insert* of a seen identifier happen under one lock, closing a TOCTOU race where two concurrent identical items could both pass the "not yet seen" check before either recorded itself. The closest analog in Chainlink is the JWT replay-protection cache used by the gateway's internet-facing workflow HTTP trigger path.

### Title
Non-atomic check-then-record in JWT replay-protection cache allows replay bypass - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
The `jwtReplayCache` used to prevent replay of JWT-authenticated workflow trigger requests exposes its "seen" check (`isReplay`) and its "record" operation (`recordUsage`) as two separate methods, each independently acquiring and releasing its own lock. There is no single atomic check-and-insert operation, so concurrent requests bearing the same JWT ID (`jti`) can both pass the replay check before either records the ID.

### Finding Description
`jwtReplayCache` guards its map with `mu sync.RWMutex`, but the read and write paths are split into two independently-locked calls: [1](#0-0) 

`isReplay` takes an `RLock`, checks membership, and releases the lock; `recordUsage` separately takes a `Lock`, inserts, and releases. Because the mutex is unexported and there is no combined "check-and-record" method (unlike the pattern used elsewhere in this same codebase), any caller that calls `isReplay` and then, after JWT signature/other checks, calls `recordUsage`, has a window during which two goroutines processing the same JWT concurrently can both observe `isReplay == false` and both proceed to be treated as first-use, then both call `recordUsage`. This is architecturally identical to the pre-fix Lighthouse `Observed*` structures, which the referenced commit fixed precisely by lifting the lock to cover check+insert atomically.

For contrast, this repository already contains the correct atomic pattern in `core/capabilities/vault/request_replay_guard.go`'s `CheckAndRecord`, which performs the existence check and the insert under a single `Lock`/`Unlock` pair: [2](#0-1) 
`jwtReplayCache` lacks this equivalent combined primitive, making the race structurally unavoidable for any caller using its public API.

### Impact Explanation
JWT-based authentication in the gateway's HTTP trigger handler is documented as: "3. Authentication: Verifies JWT token (ECDSA signature) and checks authorized keys" [3](#0-2) . If replay protection can be raced, an unprivileged external caller (or a network intermediary replaying a captured JWT) could cause the same signed workflow-trigger request to be accepted more than once concurrently, defeating the replay-prevention guarantee and potentially triggering duplicate workflow executions from a single authorized JWT.

### Likelihood Explanation
Exploiting the race requires sending multiple copies of the same JWT-bearing request concurrently to the gateway — trivially achievable by an external, unprivileged client without any special access, since the gateway is the internet-facing entry point for workflow HTTP triggers.

### Recommendation
Replace the separate `isReplay`/`recordUsage` methods with a single atomic `CheckAndRecord`-style method (mirroring `RequestReplayGuard.CheckAndRecord` in `core/capabilities/vault/request_replay_guard.go`) that holds `mu.Lock()` across both the membership check and the insert, and update all call sites in `workflow_metadata_handler.go` to use the combined method instead of two separate calls.

### Proof of Concept
1. Attacker captures/replays a validly-signed JWT-authenticated workflow trigger request.
2. Attacker fires N concurrent copies of the identical request at the gateway.
3. Each goroutine handling a copy calls `jwtCache.isReplay(jti)` before any of them calls `jwtCache.recordUsage(jti)`; since these are two independently-locked calls, more than one goroutine can observe `exists == false`.
4. Multiple copies of the same JWT are treated as first-use and proceed past replay protection, each potentially triggering workflow execution/dispatch. [4](#0-3)

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L392-412)
```go
func newJWTReplayCache(cleanupPeriod time.Duration) *jwtReplayCache {
	return &jwtReplayCache{
		cache:         make(map[string]time.Time),
		cleanupPeriod: cleanupPeriod,
	}
}

func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L79-82)
```markdown
1. **Request Validation**: Validates JSON-RPC format, method, and parameters
2. **Workflow Resolution**: Resolves workflow ID from selector (ID, owner, name, tag)
3. **Authentication**: Verifies JWT token (ECDSA signature) and checks authorized keys
4. **Rate Limiting**: Enforces per-workflow-owner rate limits
```
