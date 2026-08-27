### Title
Global `maxCacheSize` in `requestCache` has no per-sender quota, allowing a single low-privileged signer to exhaust the shared request cache and deny service to other users - ([File: core/services/gateway/handlers/common/requestcache.go])

### Summary
`requestCache.NewRequest` enforces only a single global size check (`len(c.cache) >= int(c.maxCacheSize)`) under one mutex, with the cache key being `{sender, messageId}` but no accounting per sender. Any signer able to produce valid signed gateway messages (an "authorized" but low-privileged sender, e.g. a vault-authorized owner or any address whose signed request passes upstream `Message.Validate()`/authorization) can flood the cache with unique `MessageId`s to permanently fill the fixed-size cache, causing `NewRequest` to return `errors.New("request cache is full")` for all other legitimate senders on that DON/handler.

### Finding Description
`NewRequest` computes `key := globalId{request.Body.Sender, request.Body.MessageId}` and checks only the aggregate cache length against a single `maxCacheSize` constant configured at construction time via `NewRequestCache[T]` [1](#0-0) . There is no per-sender counter or quota — the `mu sync.Mutex` only guards the shared map, not fairness across senders. Since each `pendingRequest` remains in the cache until either a full aggregated response arrives or the configured `timeout` elapses (`time.AfterFunc(c.timeout, ...)`), a sender can keep the cache saturated for the whole timeout window simply by submitting distinct `MessageId`s that never receive a matching node response (bogus/never-answered IDs), at negligible cost, since message signing and `MessageId` uniqueness are the only constraints (`api.Message.Validate()` bounds `MessageId` length but does not prevent an attacker from generating arbitrary unique IDs) [2](#0-1) . Once `len(c.cache) >= maxCacheSize`, every other sender's `NewRequest` call — regardless of who they are — fails with `"request cache is full"`, denying legitimate users' requests from being registered at all.

### Impact Explanation
This is a quota/fairness bypass leading to denial of service for all users sharing the same handler's request cache instance: legitimate senders cannot get their requests admitted while an attacker (who only needs to be able to produce validly-signed gateway messages, not any elevated privilege) keeps the shared pool full. This matches the "allowlist/quota bypass" impact class explicitly in scope, and results in real availability impact on gateway-mediated capability requests (e.g., vault, HTTP trigger request/response correlation) for arbitrary other users.

### Likelihood Explanation
Feasibility is high: the attacker only needs the ability to submit signed gateway messages that reach a handler backed by this cache (there is no per-sender limit, no cost beyond producing a valid signature and a fresh `MessageId`). The attack is trivially repeatable and requires no operator/admin access — only whatever minimal authorization the specific consuming handler enforces before calling `NewRequest` (e.g., a valid signature), which is exactly the low-privilege "any address sending signed gateway requests" actor allowed under audit scope.

### Recommendation
Add a per-sender quota (e.g., a `map[sender]uint32` counter with its own configurable limit) enforced alongside the global `maxCacheSize`, so no single sender can occupy more than a bounded fraction of the shared cache. Consider also rate-limiting `NewRequest` calls per sender/messageId pattern before they reach the cache.

### Proof of Concept
Extend `core/services/gateway/handlers/common/requestcache_test.go`:
1. Create `cache := common.NewRequestCache[requestState](time.Hour, N)` with small `N` (e.g., 10).
2. From a single `sender` (e.g., `"0xattacker"`), loop and call `cache.NewRequest(lggr, &api.Message{Body: api.MessageBody{MessageId: fmt.Sprintf("id-%d", i), Sender: "0xattacker"}}, callback, initialState)` for `i` in `0..N-1`, asserting all succeed.
3. Call `cache.NewRequest` for a different legitimate `sender := "0xvictim"` with a fresh `MessageId` and assert it returns the `"request cache is full"` error, demonstrating that the victim's unrelated request is blocked purely due to the attacker's flooding, with no per-sender isolation.

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L46-66)
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
```

**File:** core/services/gateway/api/message.go (L54-66)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```
