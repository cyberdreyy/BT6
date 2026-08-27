### Title
Response cache in the HTTP capability gateway handler ignores `WorkflowID`, allowing cross-workflow response confusion despite documented workflow isolation - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
Similar to the `DelFiPrice` bug where the specification ("whenever a post happens, the official price is recalculated") was not strictly enforced by the implementation, the `v2` HTTP capability gateway handler's response cache documentation and design promise **workflow-scoped** caching, but the actual cache key computation does not include `WorkflowID`, breaking the documented isolation guarantee.

### Finding Description
The `gatewayHandler`'s outbound HTTP response cache is documented as workflow-isolated: the package README states "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [1](#0-0) .

However, `responseCache` keys its map purely by `req.Hash()`, and the cache's own doc comment says the hash covers "method, URL, headers, body, workflowOwner" — notably omitting `WorkflowID` [2](#0-1) . Both `Fetch` and `Set` use `req.Hash()` as the sole cache key without any additional workflow-ID scoping [3](#0-2) .

This repo's own test suite confirms the behavior is intentional/known: `TestRequestHash` explicitly asserts that requests with different `WorkflowID` values produce the **same** hash, while only `WorkflowOwner` differentiates the hash [4](#0-3) .

The cache is populated and read directly from node-supplied `OutboundHTTPRequest` fields in `makeOutgoingRequest`, where `req.CacheSettings.Store`/`MaxAgeMs` (also client-controlled) determine whether a cached entry is read (`Fetch`) or written (`Set`) [5](#0-4) .

Consequently, two different workflows belonging to the **same** `WorkflowOwner` that issue outbound HTTP requests with identical method/URL/headers/body but different `WorkflowID` will collide on the same cache entry: a response fetched (and possibly attacker-influenced, since it originates from a workflow-controlled outbound request) for Workflow A can be transparently served to Workflow B. This is a direct parallel to the `DelFiPrice` issue: the code diverges from its own specification about "when data is recalculated/scoped," leaving stale/incorrect state readable across contexts that are supposed to be isolated.

### Impact Explanation
If two workflows under one owner target the same external endpoint with the same request shape, one workflow can receive HTTP response data that was actually fetched (and cached) in the context of a different workflow. This can lead to:
- Cross-workflow data leakage/response confusion, violating the explicit "Workflow Isolation" guarantee documented for this handler.
- A malicious or compromised workflow deliberately priming the shared cache (via `CacheSettings.Store=true` with a crafted first request) to have another, unrelated workflow of the same owner consume manipulated/injected response data on a subsequent identical request, since `WorkflowID` plays no role in cache key derivation.

This is a data-integrity/response-confusion issue in a gateway component explicitly designed and documented to prevent exactly this class of leakage.

### Likelihood Explanation
Exploitation requires an attacker-controlled workflow (same owner, different `WorkflowID`) to craft an `OutboundHTTPRequest` with the exact same `Method`, `URL`, `Headers`, and `Body` as the victim workflow's request, and for caching (`Store: true`) to be enabled with a non-zero `MaxAgeMs` window. Since workflow definitions/templates for a given owner are often known or shared, and the cache TTL defaults to 10 minutes [6](#0-5) , this is plausible for realistic multi-workflow deployments, though it does not cross owner/tenant boundaries since `WorkflowOwner` is part of the hash.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`) in the cache key computation, or scope the `responseCache` map by `(WorkflowID, req.Hash())` rather than by `req.Hash()` alone, so that the implementation matches the documented "workflow ID and request hash"-keyed isolation guarantee.

### Proof of Concept
1. Workflow A (WorkflowOwner=`0xOwner`, WorkflowID=`wfA`) issues an `OutboundHTTPRequest{Method: "GET", URL: "https://api.example.com/data", CacheSettings:{Store:true, MaxAgeMs: 600000}}`; the gateway fetches and caches the response keyed by `req.Hash()` (excludes `WorkflowID`) [7](#0-6) .
2. Workflow B (same `WorkflowOwner=0xOwner`, WorkflowID=`wfB`) issues an identical `OutboundHTTPRequest` (same Method/URL/Headers/Body) with `MaxAgeMs>0`.
3. `Fetch` computes the same `req.Hash()` (since `WorkflowID` is excluded) and returns Workflow A's cached response to Workflow B [8](#0-7) , confirmed directly by the existing unit test `TestRequestHash/"having different workflowID results in same Hash"` [9](#0-8) .

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-120)
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
}

// Set caches a response if it is cacheable (2xx or 4xx and cache is empty or expired for the given request)
func (rc *responseCache) Set(req gateway.OutboundHTTPRequest, response gateway.OutboundHTTPResponse) {
	rc.cacheMu.Lock()
	defer rc.cacheMu.Unlock()
	if isCacheableStatusCode(response.StatusCode) && rc.isExpiredOrNotCached(req) {
		rc.cache[req.Hash()] = &cachedResponse{
			response: response,
			storedAt: time.Now(),
		}
	}
}
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L139-175)
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

	t.Run("having same workflowOwner results in the same Hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req1.WorkflowOwner = "workflow-owner-123"

		req2 := createTestRequest("GET", "https://example.com")
		req2.WorkflowOwner = "workflow-owner-123"

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.Equal(t, hash1, hash2, "Hash should be the same for identical requests")
	})

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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L40-40)
```go
	defaultOutboundRequestCacheTTLMs     = 1000 * 60 * 10      // 10 minutes
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L422-441)
```go
	sendResponseTimeout := time.Duration(defaultSendResponseTimeoutMs) * time.Millisecond

	// send response to node async
	h.wg.Go(func() {
		// not cancelled when parent is cancelled to ensure the goroutine can finish
		baseCtx := context.WithoutCancel(ctx)
		httpCtx, httpCancel := context.WithTimeout(baseCtx, timeout)
		defer httpCancel()
		l := logger.With(h.lggr, "requestID", requestID, "method", req.Method, "timeout", req.TimeoutMs)
		var outboundResp gateway_common.OutboundHTTPResponse
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
