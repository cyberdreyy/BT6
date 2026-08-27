### Title
Response cache key ignores WorkflowID, enabling cross-workflow response reuse in the HTTP capability gateway handler - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The gateway's `responseCache`, used to serve cached `OutboundHTTPRequest`/`OutboundHTTPResponse` pairs for the HTTP capability, keys entries on a hash that intentionally excludes `WorkflowID` (and `CacheSettings`), keying only on method/URL/headers/body/`WorkflowOwner`. This is the same bug class as the reported vault issue: two logically distinct entities (there, different tokens with different values; here, different workflows) are treated as fungible/interchangeable by the caching/accounting layer, allowing a "cheap" cached response with permissive `CacheSettings` to satisfy a request from an unrelated workflow that would otherwise require a fresh fetch.

### Finding Description
`responseCache.Fetch`/`Set` compute `cacheKey := req.Hash()` [1](#0-0) , and the comment explicitly documents that the hash is built from "method, URL, headers, body, workflowOwner" [2](#0-1) . The accompanying test suite confirms `WorkflowID` is deliberately excluded from the hash ("Hash should be the same regardless of WorkflowID") [3](#0-2) , while `CacheSettings` (including `MaxAgeMs`/`Store`) are also excluded from the hash [4](#0-3) .

This means any two workflows owned by the same `WorkflowOwner` that issue an outbound HTTP action with identical method/URL/headers/body share one cache slot, regardless of `WorkflowID`. `Fetch` is invoked per-request with that request's own `CacheSettings.MaxAgeMs`/`Store` [5](#0-4) , but the underlying cache entry was written under whatever `CacheSettings` the *first* request happened to use — since settings aren't part of the key, a workflow that sets `Store:false` (or a very short un-cacheable TTL) can still unknowingly read (or seed) a shared entry populated by a different workflow's request with `Store:true`. The project's own README claims "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [6](#0-5) , which directly contradicts the actual `Hash()` behavior verified by the tests — this is a design/documentation-vs-implementation mismatch, analogous to the vault's design flaw of treating distinct assets as fungible.

### Impact Explanation
Because caching is scoped only by `(method, URL, headers, body, workflowOwner)` and not by `WorkflowID`, a malicious or compromised workflow belonging to the same owner (or any workflow whose crafted request collides on this tuple) can:
- Poison the cache for a sibling workflow by pre-populating an entry with attacker-controlled response content (via a manipulated outbound target or a race with `Store:true`), which a second, victim workflow later retrieves via `Fetch` without knowing it is stale/attacker-influenced data, since `Fetch` only checks `storedAt` age against its own `MaxAgeMs`, not workflow identity.
- Cause unintended data leakage between workflows: a response fetched (and possibly containing owner-scoped but not workflow-scoped secrets/headers) for workflow A can be transparently served to workflow B under the same owner without a fresh HTTP call, violating the intended "Workflow Isolation" contract stated in the docs.

The severity is bounded by the requirement that the colliding requests share the same `WorkflowOwner` and identical method/URL/headers/body, and that only 2xx/4xx responses are cacheable [7](#0-6) . This is not a full authentication bypass, but it is a concrete cross-workflow response confusion / cache-key collision bug reachable from unprivileged workflow-owner-controlled requests through the gateway's node-facing HTTP capability path.

### Likelihood Explanation
Likelihood is moderate: it requires an owner to run (or compromise) two workflows that issue requests with identical method/URL/headers/body but different `CacheSettings`/`WorkflowID` — a realistic scenario for shared integrations (e.g., two workflows polling the same external API endpoint under one owner). No cross-owner exploitation is possible since `WorkflowOwner` is part of the hash, which limits blast radius but does not eliminate the flaw for the documented "workflow isolation" guarantee.

### Recommendation
Include `WorkflowID` (and ideally the `CacheSettings` that govern cacheability, such as `Store`) in the cache key computed by `OutboundHTTPRequest.Hash()`, or explicitly namespace the `responseCache` map by `WorkflowID` in addition to the request hash, so that cache entries cannot be shared across workflows. Update the README's "Workflow Isolation" claim to match the corrected implementation, and add a regression test asserting that different `WorkflowID`s produce different cache keys (inverting the current `TestRequestHash` "different workflowID results in same Hash" test).

### Proof of Concept
1. Workflow A (owner `0xABC`, `WorkflowID = "wf-A"`) sends an `OutboundHTTPRequest{Method:"GET", URL:"https://api.example.com/data", CacheSettings:{Store:true, MaxAgeMs:600000}}` through the gateway; the gateway calls `responseCache.Fetch`, which executes the real HTTP call and stores the response keyed by `req.Hash()` (method+URL+headers+body+owner) [8](#0-7) .
2. Workflow B (same owner `0xABC`, different `WorkflowID = "wf-B"`) sends an identical `OutboundHTTPRequest` (same method/URL/headers/body) with `CacheSettings:{Store:true, MaxAgeMs:600000}}`.
3. Because `Hash()` ignores `WorkflowID` (verified by `TestRequestHash`'s "having different workflowID results in same Hash" case) [3](#0-2) , `responseCache.Fetch` returns Workflow A's cached response to Workflow B without making a fresh HTTP call, violating the documented per-workflow isolation guarantee [6](#0-5) .

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L15-17)
```go
// responseCache is a thread-safe cache for storing HTTP responses.
// It uses a map to store responses keyed by a hash of the request (method, URL, headers, body, workflowOwner).
type responseCache struct {
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L40-44)
```go
// isCacheableStatusCode returns true if the HTTP status code indicates a cacheable response.
// This includes successful responses (2xx) and client errors (4xx)
func isCacheableStatusCode(statusCode int) bool {
	return (statusCode >= 200 && statusCode < 300) || (statusCode >= 400 && statusCode < 500)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-107)
```go
func (rc *responseCache) Fetch(ctx context.Context, req gateway.OutboundHTTPRequest, fetchFn func() gateway.OutboundHTTPResponse, storeOnFetch bool) gateway.OutboundHTTPResponse {
	cacheKey := req.Hash()
	cacheMaxAge := time.Duration(req.CacheSettings.MaxAgeMs) * time.Millisecond

	// Fast path: check cache without singleflight overhead.
	rc.cacheMu.RLock()
	cachedResp, exists := rc.cache[cacheKey]
	rc.cacheMu.RUnlock()
	if exists && cachedResp.storedAt.Add(cacheMaxAge).After(time.Now()) {
		rc.metrics.IncrementCacheHitCount(ctx, rc.lggr)
		return cachedResp.response
	}

	// Slow path: singleflight deduplicates concurrent fetches per key.
	// Cache check + store happen inside the flight so the key isn't released
	// until the result is cached, closing the race window between singleflight
	// completion and cache write.
	result, _, _ := rc.flight.Do(cacheKey, func() (any, error) {
		// Re-check cache: a previous flight may have just stored the result.
		rc.cacheMu.RLock()
		cachedResp, exists := rc.cache[cacheKey]
		rc.cacheMu.RUnlock()
		if exists && cachedResp.storedAt.Add(cacheMaxAge).After(time.Now()) {
			rc.metrics.IncrementCacheHitCount(ctx, rc.lggr)
			return cachedResp.response, nil
		}

		response := fetchFn()

		if storeOnFetch && isCacheableStatusCode(response.StatusCode) {
			rc.cacheMu.Lock()
			rc.cache[cacheKey] = &cachedResponse{
				response: response,
				storedAt: time.Now(),
			}
			rc.cacheMu.Unlock()
		}

		return response, nil
	})

	return result.(gateway.OutboundHTTPResponse)
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L121-137)
```go
	t.Run("having different cacheSettings results in the same Hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req1.CacheSettings = gateway_common.CacheSettings{
			MaxAgeMs: 5000,
			Store:    true,
		}

		req2 := createTestRequest("GET", "https://example.com")
		req2.CacheSettings = gateway_common.CacheSettings{
			MaxAgeMs: 10000,
			Store:    false,
		}

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.Equal(t, hash1, hash2, "Hash should be the same regardless of CacheSettings")
	})
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L139-149)
```go
	t.Run("having different workflowID results in same Hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req1.WorkflowID = "workflow-123"

		req2 := createTestRequest("GET", "https://example.com")
		req2.WorkflowID = "workflow-456"

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")
	})
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L433-441)
```go
		if req.CacheSettings.MaxAgeMs > 0 {
			h.metrics.IncrementCacheReadCount(ctx, h.lggr)
			outboundResp = h.responseCache.Fetch(httpCtx, req, callback, req.CacheSettings.Store)
		} else {
			outboundResp = callback()
			if req.CacheSettings.Store {
				h.responseCache.Set(req, outboundResp)
			}
		}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L65-72)
```markdown
### 3.2 Caching Behavior

- **Cacheable Responses**: 2xx (success) and 4xx (client error) status codes.
- **Cache TTL**: Configurable, default 10 minutes
- **Cache Key**: Generated from workflow ID and request hash
- **Cache Invalidation**: Time-based expiration with periodic cleanup
- **Cache Strategy**: All cacheable responses are cached; Non-zero `CacheSettings.MaxAgeMs` determines whether to return a cached value or make a fresh request
- **Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage
```
