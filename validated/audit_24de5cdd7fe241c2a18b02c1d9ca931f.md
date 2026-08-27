Based on my investigation, the `Hash()` implementation for `gateway_common.OutboundHTTPRequest` lives in the external `chainlink-common` module and is not present in this repo's index, so I cannot fully verify its exact field composition from source. However, the test suite in this repo explicitly documents and asserts its behavior, which is sufficient to establish the root cause.

### Title
Response Cache Key Omits WorkflowID, Contradicting Documented Workflow-Scoped Isolation and Enabling Cross-Workflow Response Confusion - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The HTTP Handlers V2 gateway component caches outbound HTTP responses keyed by `gateway_common.OutboundHTTPRequest.Hash()` [1](#0-0) . The component's own documentation states cache entries are isolated per-workflow ("Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage") [2](#0-1) , but the test suite proves the hash is invariant to `WorkflowID` and only varies with `WorkflowOwner` [3](#0-2) .

### Finding Description
This maps to the same bug class as the external report: unvalidated/under-scoped external inputs (here, `WorkflowID` in cache-key derivation) lead to unpredictable, cross-context outcomes — in this case, cross-workflow response confusion instead of cross-chain fund loss. An unprivileged workflow-owning client that runs multiple distinct workflows under the same owner can trigger HTTP actions with `CacheSettings.Store = true` from workflow A and workflow B. If both requests hash identically (same method/URL/headers/body/owner, since `WorkflowID` is excluded from the hash per the test at lines 139-149), workflow B's `Fetch` call at `responseCache.Fetch` will silently return workflow A's cached response for up to the cache TTL (`MaxAgeMs`), rather than executing a fresh outbound request. The call sites in `makeOutgoingRequest` pass the whole `req` (including its `WorkflowID`) into `Set`/`Fetch` [4](#0-3) , but the cache internals only use `req.Hash()` as the key [5](#0-4) [6](#0-5) .

### Impact Explanation
A workflow owner (unprivileged actor relative to other workflows/tenants they run) could receive stale or mismatched data belonging to a different workflow context, corrupting that workflow's execution logic, decision-making, or any on-chain action gated by the HTTP action's result. This is a "cross-user response confusion" analog per the validation criteria, though the blast radius is scoped to the same `WorkflowOwner` (the cache key does differentiate different owners, per the test at lines 163-175), so it is not a fully cross-tenant leak but a cross-workflow leak within one owner's workflows.

### Likelihood Explanation
Likelihood is moderate: it requires (a) the same owner running two or more workflows that issue HTTP actions to the same URL/method/headers/body with `CacheSettings.Store=true` and non-zero `MaxAgeMs`, and (b) those workflows expecting workflow-specific responses despite an identical request signature. This is plausible in shared integration endpoints (e.g., generic price/data feeds) reused across a developer's multiple workflows with parameterization embedded outside the hashed fields.

### Recommendation
Include `WorkflowID` in the `OutboundHTTPRequest.Hash()` computation (or otherwise namespace the cache key by `WorkflowID` in `responseCache.Fetch`/`Set`) so that the implementation matches the documented isolation guarantee, preventing cross-workflow cache poisoning/confusion.

### Proof of Concept
Using the existing test helper, construct two requests differing only by `WorkflowID`:
```go
req1 := createTestRequest("GET", "https://example.com")
req1.WorkflowID = "workflow-123"

req2 := createTestRequest("GET", "https://example.com")
req2.WorkflowID = "workflow-456"

hash1 := req1.Hash()
hash2 := req2.Hash()
// hash1 == hash2, confirmed by existing test:
``` [7](#0-6) 

If workflow-123 calls `responseCache.Set(req1, respA)` and later workflow-456 calls `responseCache.Fetch(ctx, req2, fetchFn, true)` within the TTL window, `fetchFn` is never invoked and `respA` (workflow-123's response) is returned to workflow-456, as shown by the general cache-hit behavior test [8](#0-7) .

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L46-54)
```go
// isExpiredOrNotCached returns true if the cached response is expired or not cached.
// IMPORTANT: this method does not lock the cache map. MUST be called with cacheMu write-locked.
func (rc *responseCache) isExpiredOrNotCached(req gateway.OutboundHTTPRequest) bool {
	cachedResp, exists := rc.cache[req.Hash()]
	if !exists || time.Now().After(cachedResp.storedAt.Add(rc.ttl)) {
		return true
	}
	return false
}
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-68)
```go
func (rc *responseCache) Fetch(ctx context.Context, req gateway.OutboundHTTPRequest, fetchFn func() gateway.OutboundHTTPResponse, storeOnFetch bool) gateway.OutboundHTTPResponse {
	cacheKey := req.Hash()
	cacheMaxAge := time.Duration(req.CacheSettings.MaxAgeMs) * time.Millisecond
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L110-120)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L230-250)
```go
	t.Run("returns cached response when cache hit", func(t *testing.T) {
		req := createTestRequest("GET", "https://example.com/hit")
		cachedResp := createTestResponse(200, "cached data")

		// Pre-populate cache
		cache.cache[req.Hash()] = &cachedResponse{
			response: cachedResp,
			storedAt: time.Now(),
		}

		var fetchCalled bool
		fetchFn := func() gateway_common.OutboundHTTPResponse {
			fetchCalled = true
			return createTestResponse(200, "should not be called")
		}

		result := cache.Fetch(t.Context(), req, fetchFn, true)

		require.False(t, fetchCalled, "fetchFn should not be called on cache hit")
		require.Equal(t, cachedResp, result)
	})
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
