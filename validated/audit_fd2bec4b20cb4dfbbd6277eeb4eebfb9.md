### Title
Gateway HTTP response cache is not scoped by workflow ID, allowing cross-workflow cached response reuse (only workflow owner is included in the cache key) - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The AlgebraPool bug is a "shared mutable state set by an earlier action affects the outcome/fee applied to a later, unrelated action within the same scope." The closest reachable analog in this codebase is the gateway's `responseCache`, which caches outbound HTTP action responses keyed by `OutboundHTTPRequest.Hash()`. Test evidence shows the hash intentionally excludes `WorkflowID` (only `WorkflowOwner` participates), so a cached response produced for one workflow can be served to a request from a *different* workflow under the same owner, as long as method/URL/headers/body match, even though the README documents "Workflow Isolation: Cache entries are scoped by workflow ID."

### Finding Description
`responseCache.Fetch`/`Set` key their in-memory map by `req.Hash()` [1](#0-0) . The struct comment states the hash is derived from "method, URL, headers, body, workflowOwner" [2](#0-1) , and this is confirmed by test cases: identical requests with different `WorkflowID` produce the **same** hash, while different `WorkflowOwner` produces a **different** hash [3](#0-2) .

However, the module's own documentation asserts stronger isolation than what is implemented: "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [4](#0-3) . In reality, `WorkflowID` is not part of the key, so any two workflows owned by the same account that happen to issue an HTTP action with identical method/URL/headers/body will hit the same cache entry (`h.responseCache.Fetch`/`Set` called from `makeOutgoingRequest`) [5](#0-4) .

This mirrors the AlgebraPool bug class: state established by one actor's action (workflow A's HTTP fetch/response, cached) is transparently reused to satisfy a subsequent, distinct actor's action (workflow B's HTTP action) whose outcome the requester did not expect to be tied to a different workflow's prior request/response.

### Impact Explanation
If an owner runs multiple workflows that call the same external endpoint with the same static request shape (a common pattern, e.g., a shared price/config API) but expect independently fresh results per workflow (e.g., due to different `CacheSettings.MaxAgeMs`, different execution timing, or expecting per-workflow isolation as documented), one workflow can receive a response that was actually fetched on behalf of a different workflow. This is a cross-workflow response confusion: a workflow could act on stale or foreign data it did not request, potentially triggering incorrect downstream on-chain actions, similar to how the AlgebraPool flashloan fee was unexpectedly derived from an unrelated prior transaction's state. It also violates the documented isolation guarantee relied upon by node operators/workflow authors, which is a correctness/trust issue, though it does not bypass authentication or leak secrets by itself (secrets like MTLS credentials are not part of the response body cached).

### Likelihood Explanation
Likelihood is moderate: it requires (a) the same account to own two or more concurrently running workflows, and (b) those workflows issuing outbound HTTP actions with identical method/URL/headers/body but relying on per-workflow cache isolation as documented. This is plausible for common integration patterns (shared external API endpoints reused across workflows) and requires no privileged access — any workflow author under the same owner can trigger it unintentionally, or an adversarial workflow (deployed by the same owner, or via a compromised/second workflow) can deliberately prime the cache to serve stale/attacker-controlled data to a sibling workflow expecting fresh results.

### Recommendation
Include `WorkflowID` (not just `WorkflowOwner`) in `OutboundHTTPRequest.Hash()`'s cache key so that cache entries are truly scoped per workflow, matching the documented behavior in `core/services/gateway/handlers/capabilities/v2/README.md`. Alternatively, if cross-workflow sharing under the same owner is intentional for efficiency, update the documentation to accurately describe owner-level (not workflow-level) cache scoping, and ensure workflow authors are not led to assume per-workflow freshness guarantees they don't actually get.

### Proof of Concept
1. Workflow A (owner `0xabc`, `WorkflowID = "wf-A"`) issues an `OutboundHTTPRequest{Method: "GET", URL: "https://api.example.com/data", CacheSettings: {Store: true, MaxAgeMs: 600000}}`. The gateway fetches and caches the response via `h.responseCache.Fetch` [6](#0-5) .
2. Workflow B (same owner `0xabc`, `WorkflowID = "wf-B"`) issues an outbound HTTP action with the identical method/URL/headers/body (same endpoint, different workflow) and `CacheSettings.MaxAgeMs > 0`.
3. Because `Hash()` ignores `WorkflowID` (proven by `TestRequestHash`'s "having different workflowID results in same Hash" case [3](#0-2) ), workflow B's `Fetch` call hits the cache entry stored by workflow A and returns workflow A's response without making a fresh HTTP call, contradicting the documented "cache entries are scoped by workflow ID" guarantee.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L424-441)
```go
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
