### Title
Outbound HTTP Response Cache Is Not Actually Scoped by Workflow ID Despite Documented Isolation Guarantee - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The gateway's HTTP Action response cache is documented as being workflow-isolated ("Cache entries are scoped by workflow ID to prevent cross-workflow data leakage"), but the cache key derived from `OutboundHTTPRequest.Hash()` does not actually include the `WorkflowID` field — it is only differentiated by method, URL, headers, body and `WorkflowOwner`. This mirrors the Pepper `mint()` bug pattern: a limiting/scoping mechanism (there, a supply cap; here, a cache isolation boundary) is enforced using an aggregate/coarser key than the one the design actually requires, silently breaking the intended guarantee.

### Finding Description
The `responseCache` used by `gatewayHandler` for outbound HTTP Action caching keys its map by `req.Hash()`: [1](#0-0) [2](#0-1) 

The struct comment states the hash includes "method, URL, headers, body, workflowOwner" — notably omitting `workflowID`. This is confirmed directly by the test suite for `Hash()`, which explicitly asserts that requests with different `WorkflowID` values produce the **same** hash, while requests with different `WorkflowOwner` values produce different hashes: [3](#0-2) [4](#0-3) 

Yet the component's own README explicitly documents the opposite guarantee — that caching is workflow-scoped and isolates workflows from each other: [5](#0-4) 

The cache read/write path (`makeOutgoingRequest` → `Fetch`/`Set`) applies this hash uniformly regardless of which workflow issued the request: [6](#0-5) 

As a result, any two workflows sharing the same owner address that happen to issue an HTTP Action with the same method/URL/headers/body (a very plausible occurrence for common integrations, e.g. identical price-feed or webhook calls) will read and write the exact same cache entry, even though they are logically distinct, independently-scheduled workflow executions.

### Impact Explanation
This breaks the workflow-isolation invariant the gateway advertises for its response cache. A workflow can receive a cached HTTP response that was actually fetched on behalf of a *different* workflow (same owner) — i.e., cross-workflow response confusion. Depending on what the external endpoint returns (e.g., data that is supposed to be scoped per-execution, or responses influenced by request context not captured in the hash, such as differing trigger metadata), one workflow's action result can leak into another's execution, potentially affecting downstream on-chain actions or state derived from that HTTP Action output. This falls squarely under the accepted "cross-user response confusion" class of issue this scan is looking for, since the enforcement key (hash) does not match the documented/intended isolation scope (per-workflow), analogous to Pepper's mint cap being computed over the wrong aggregate (`totalSupply()` instead of a dedicated minter-role counter).

### Likelihood Explanation
Likelihood is moderate: it requires two workflows under the same `WorkflowOwner` to issue byte-identical outbound HTTP Action requests (method, URL, headers, body) with `CacheSettings.Store`/`MaxAgeMs` enabled — a realistic scenario for owners running multiple similar workflows against the same external API (e.g., pulling the same public price feed) but expecting execution isolation per the documented guarantee.

### Recommendation
Include `WorkflowID` in the cache key computed by `OutboundHTTPRequest.Hash()` (or otherwise incorporate it into the `responseCache` key), matching the documented behavior in the README, so that cache entries are strictly scoped per workflow rather than per owner. Add/adjust unit tests in `response_cache_test.go` to assert that different `WorkflowID` values with otherwise identical requests produce different hashes, replacing the current test that intentionally verifies the opposite.

### Proof of Concept
1. Configure two workflows, `wf-1` and `wf-2`, owned by the same `WorkflowOwner`.
2. Have `wf-1` issue an `OutboundHTTPRequest{Method: "GET", URL: "https://example.com/api", CacheSettings: {Store: true, MaxAgeMs: 600000}}`; the gateway fetches and caches the response under `req.Hash()`.
3. Have `wf-2` issue the identical `OutboundHTTPRequest` (same method/URL/headers/body/owner) with a different `WorkflowID`.
4. Per `response_cache_test.go`'s `TestRequestHash` ("having different workflowID results in same Hash"), `wf-2`'s request hashes to the same cache key as `wf-1`'s, so `wf-2` receives `wf-1`'s cached response via `responseCache.Fetch` without the gateway making a fresh request — despite the README's claim that "Cache entries are scoped by workflow ID to prevent cross-workflow data leakage."

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L15-24)
```go
// responseCache is a thread-safe cache for storing HTTP responses.
// It uses a map to store responses keyed by a hash of the request (method, URL, headers, body, workflowOwner).
type responseCache struct {
	cacheMu sync.RWMutex
	cache   map[string]*cachedResponse
	flight  singleflight.Group
	lggr    logger.Logger
	ttl     time.Duration
	metrics *metrics.Metrics
}
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-77)
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L163-175)
```go
	t.Run("having different workflowOwner results in different Hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req1.WorkflowOwner = "workflow-owner-123"

		req2 := createTestRequest("GET", "https://example.com")
		req2.WorkflowOwner = "workflow-owner-456"

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.NotEqual(t, hash1, hash2, "Hash should be different for different workflow owner")
		require.NotEmpty(t, hash1, "Hash should not be empty")
		require.NotEmpty(t, hash2, "Hash should not be empty")
	})
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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L432-441)
```go
		callback := h.createHTTPRequestCallback(httpCtx, requestID, httpReq, req)
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
