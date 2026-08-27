### Title
HTTP capability gateway response cache is keyed by `WorkflowOwner` instead of `WorkflowID`, causing cross-workflow response cache confusion - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The Blueberry finding used `uToken.balanceOf(this)` instead of `vault.balanceOf(this)` when determining the amount to deposit — the wrong "identity" was used as the scope for an operation, leading to funds meant for one context leaking into another. The chainlink analog is the HTTP Actions `responseCache` in the CRE gateway: cache entries are supposed to be workflow-scoped, but the actual cache key (`OutboundHTTPRequest.Hash()`) is derived from `WorkflowOwner` (plus method/URL/headers/body), and explicitly ignores `WorkflowID`, contradicting the documented "workflow ID" scoping.

### Finding Description
`gatewayHandler.makeOutgoingRequest` reads an `OutboundHTTPRequest` from a workflow node and, when `CacheSettings.MaxAgeMs > 0`, fetches/stores responses via `h.responseCache.Fetch`/`Set`, keyed on `req.Hash()`: [1](#0-0) 

The `responseCache` struct comment states the key is "a hash of the request (method, URL, headers, body, workflowOwner)": [2](#0-1) 

`Set`/`isExpiredOrNotCached` both key strictly by `req.Hash()`: [3](#0-2) [4](#0-3) 

The repo's own test suite proves `Hash()` deliberately excludes `WorkflowID` while including `WorkflowOwner`: [5](#0-4) [6](#0-5) 

This directly contradicts the module's own documented design, which states the cache key should be based on "workflow ID" and that "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage": [7](#0-6) 

Because `WorkflowOwner` (an EVM address, potentially the same across many workflows belonging to the same author/tenant) is used instead of `WorkflowID` (a unique per-workflow identifier), any two distinct workflows owned by the same address that issue an HTTP Action with the same method/URL/headers/body will collide on the same cache entry. The first workflow's fetched (and possibly workflow-specific) response can be transparently served to a completely different workflow, as long as `CacheSettings.Store`/`MaxAgeMs` are set.

### Impact Explanation
This is a cross-workflow response confusion vulnerability: a workflow (an unprivileged, gateway-facing client that only needs to control an `OutboundHTTPRequest` payload for a shared workflow owner) can receive HTTP action response data that was fetched/cached for a sibling workflow it does not otherwise have visibility into, and vice versa — one workflow can effectively poison the cache and force a different workflow under the same owner to receive stale or attacker-influenced data instead of hitting the real external endpoint. This violates the intended workflow isolation explicitly promised by the module's design documentation.

### Likelihood Explanation
Reachability requires only that two workflows share the same `WorkflowOwner` and issue outbound HTTP actions with identical method/URL/headers/body and non-zero `CacheSettings.MaxAgeMs`/`Store` — an entirely plausible, unprivileged, and common scenario (the same tenant/owner running multiple workflows against the same external API). No node compromise or special privilege is required; only normal workflow execution triggering `makeOutgoingRequest`.

### Recommendation
Include `WorkflowID` (not just `WorkflowOwner`) as part of the cache key computed by `OutboundHTTPRequest.Hash()`, so cache entries are truly scoped per-workflow as the design documentation states, matching the "Workflow Isolation" guarantee.

### Proof of Concept
1. Two workflows, `wf-A` and `wf-B`, share the same `WorkflowOwner`.
2. `wf-A` issues an `OutboundHTTPRequest{Method: "GET", URL: "https://api.example.com/data", CacheSettings:{Store:true, MaxAgeMs: 600000}}`; the gateway fetches and caches the response under `req.Hash()` (owner+method+url+headers+body only) via `Set`.
3. `wf-B`, with a different `WorkflowID` but the same `WorkflowOwner` and an identical request shape, sends the same request with `MaxAgeMs>0`; `Fetch` computes the same `req.Hash()` (since `WorkflowID` is excluded per the test at lines 139-149 of `response_cache_test.go`) and returns `wf-A`'s cached response to `wf-B` without making a new HTTP call — confirmed directly by the existing unit test asserting "Hash should be the same regardless of WorkflowID".

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L431-441)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L151-175)
```go
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
