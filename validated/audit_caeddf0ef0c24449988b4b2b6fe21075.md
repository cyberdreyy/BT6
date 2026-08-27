### Title
Global request cache allows single-sender flooding to exhaust `maxCacheSize`, causing legitimate users' `NewRequest` calls to fail - ([File: core/services/gateway/handlers/common/requestcache.go])

### Summary
`requestCache.NewRequest` enforces only a single global `maxCacheSize` limit shared across all senders, with no per-sender quota or rate limiting. Any client capable of generating distinct signed `(sender, messageId)` pairs can fill the shared `cache` map up to `maxCacheSize`, causing subsequent `NewRequest` calls from other legitimate users to fail with `"request cache is full"`.

### Finding Description
`requestCache[T]` stores pending requests in a single map keyed by `globalId{sender, id}` and enforces capacity via a single counter check: [1](#0-0) 

There is no accounting per sender — `len(c.cache) >= int(c.maxCacheSize)` is a purely global check. An attacker who can produce arbitrarily many distinct `messageId` values under their own sender address (which the code's `PRECONDITIONS` state is trivial — attacker can freely generate distinct signed requests) can call `NewRequest` repeatedly to insert entries that persist until either `ProcessResponse` completes them or the `timeout` fires via `time.AfterFunc(c.timeout, ...)`. Because entries live for the full configured `timeout` before eviction, an attacker can keep the cache saturated indefinitely by continuously submitting a slightly-below-timeout stream of new distinct requests, denying cache slots to legitimate senders whose own well-formed `NewRequest` calls will then return `errors.New("request cache is full")`.

I was unable to confirm, from the indexed portion of `core/services/gateway/gateway.go`, an explicit production call site wiring `NewRequestCache`/`requestCache.NewRequest` into the gateway's user-message dispatch path (the only occurrences of `NewRequestCache` found were in `requestcache_test.go`; the currently-inspected `vault` handler uses its own separate `activeRequests` map keyed only by `req.ID`, not this generic cache). This is a limitation of the search/index coverage in this session, not a confirmation that the type is unused — `RequestCache[T]` is an exported, general-purpose interface intended for use by gateway handlers that aggregate DON responses, so the design flaw is real wherever it is instantiated, but I could not verify the exact reachable HTTP/gateway route in this pass.

### Impact Explanation
If a handler instantiates `requestCache` with a bounded `maxCacheSize`, this is a denial-of-service vector: one unprivileged attacker can starve the shared cache and block other users' otherwise-valid requests from ever entering the pending-request table, causing their requests to be rejected outright (not just delayed). This matches a service-disruption / DoS impact class rather than a privilege-escalation or fund-loss class, since no cross-user data or authorization boundary is crossed — only availability of the shared resource.

### Likelihood Explanation
Feasibility is high assuming a production caller exists: the only precondition is the ability to submit `maxCacheSize` distinct signed requests before any of them complete or time out, which requires no elevated privilege — just a valid signing key and network/API access to the gateway's message endpint. Repeatability is limited only by the configured `timeout` and `maxCacheSize`, and the attacker can sustain saturation by pipelining new requests as the timeout window advances.

### Recommendation
Add a per-sender quota (e.g., cap entries per `sender` independently of the global map, or maintain per-sender counters) in `requestCache.NewRequest`, and/or apply request-rate limiting per sender at the ingress point (`HandleUserMessage`/gateway dispatch) before requests reach the shared cache, so a single sender cannot consume the entire global capacity.

### Proof of Concept
1. In `core/services/gateway/handlers/common/requestcache_test.go`-style test, construct `NewRequestCache[T](timeout, maxCacheSize)` with a small `maxCacheSize` (e.g., 3).
2. Loop `maxCacheSize` times, calling `NewRequest` with the same attacker `sender` but distinct `MessageId` values and distinct dummy callbacks; assert all succeed (`err == nil`).
3. Call `NewRequest` once more with a different, legitimate `sender`/`MessageId` and assert it returns the error `"request cache is full"`, demonstrating the victim's well-formed request is rejected purely due to attacker-controlled entries with no per-sender isolation. [2](#0-1)

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L50-76)
```go
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
