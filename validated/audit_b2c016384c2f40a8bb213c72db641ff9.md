### Title
Gateway HTTP action response cache is keyed only by `WorkflowOwner` (not `WorkflowID`), allowing cross-workflow cached-response confusion for the same owner - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The response-cache staleness/identity check in the CRE Gateway's HTTP capability handler relies on `OutboundHTTPRequest.Hash()` as the sole determinant of "is this the same request" for caching purposes, but that hash intentionally omits `WorkflowID`, so two different workflows owned by the same address that issue the same method/URL/headers/body outbound HTTP action will share one cache entry and receive each other's cached responses.

### Finding Description
`responseCache` stores HTTP responses keyed by `req.Hash()` [1](#0-0) , and both the freshness check (`isExpiredOrNotCached`) and the `Fetch` cache-hit path use only `req.Hash()` plus a time comparison against `MaxAgeMs`/`ttl` to decide whether a previously stored response can be returned [2](#0-1) .

The comment on the struct states the hash is derived from "method, URL, headers, body, workflowOwner" [3](#0-2) , and this is confirmed by the test suite, which explicitly asserts that requests with different `WorkflowID` produce the *same* hash, while only different `WorkflowOwner` values change the hash: [4](#0-3) [5](#0-4) 

This directly contradicts the component's documented security guarantee. The handler README states the cache key is "generated from workflow ID and request hash" and that "cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [6](#0-5) . The actual implementation does not include `WorkflowID` in the cache key at all, so the documented isolation guarantee does not hold.

The vulnerable path is reachable from any workflow node acting on behalf of an unprivileged workflow owner: `makeOutgoingRequest` unmarshals the node's `OutboundHTTPRequest` and, when `CacheSettings.MaxAgeMs > 0`, calls `h.responseCache.Fetch(httpCtx, req, callback, req.CacheSettings.Store)` [7](#0-6) . Because the resulting `Fetch`/`Set` cache entries key strictly off `req.Hash()` (which excludes `WorkflowID`), any workflow belonging to the same owner that constructs an outbound HTTP action with an identical method/URL/headers/body will read the cached response produced by a different workflow.

### Impact Explanation
This is a cross-workflow response confusion bug within the gateway's unprivileged, internet-facing HTTP capability path. If two distinct workflows under the same owner (a normal, unprivileged multi-workflow tenant setup) happen to issue requests with the same method/URL/headers/body but different secrets/state-dependent parameters embedded elsewhere (e.g., via templated bodies resolved before hashing, or simply coincidentally identical requests), one workflow can receive a cached HTTP response that was actually fetched and intended for a different workflow. This can leak data between workflows that the owner intends to keep isolated (e.g., different workflow executions hitting the same endpoint but expecting workflow-specific responses) and violates the explicitly documented "Workflow Isolation" guarantee of the component.

### Likelihood Explanation
The condition is realistic and not a modeling edge case: any owner running multiple concurrent workflows that both issue caching-enabled (`CacheSettings.MaxAgeMs > 0`) outbound HTTP actions with the same method/URL/headers/body will trigger the shared-cache-entry behavior deterministically — this is proven by the codebase's own unit tests, which assert the hash collision behavior as a matter of design (`"having different workflowID results in same Hash"`), not as a discovered accident. The `Fetch` path is on the normal, non-privileged workflow HTTP action flow.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`) in `OutboundHTTPRequest.Hash()`, or otherwise scope the `responseCache` map key by `(WorkflowID, req.Hash())`, so cache lookups and stores are isolated per-workflow rather than per-owner. Update the unit test `"having different workflowID results in same Hash"` to assert inequality instead, and align the implementation with the documented behavior in `core/services/gateway/handlers/capabilities/v2/README.md`.

### Proof of Concept
1. Workflow A (WorkflowID = "workflow-123", WorkflowOwner = "owner-1") issues an `OutboundHTTPRequest` for `GET https://example.com/api` with `CacheSettings.MaxAgeMs = 5000, Store = true`. The gateway fetches the response and stores it in `responseCache` keyed by `req.Hash()` (independent of WorkflowID) [8](#0-7) .
2. Workflow B (WorkflowID = "workflow-456", same WorkflowOwner = "owner-1") issues an identical `OutboundHTTPRequest` (same method/URL/headers/body) with `CacheSettings.MaxAgeMs > 0` within the TTL window.
3. Because `req.Hash()` for workflow A and workflow B is identical (per `TestRequestHash`'s `"having different workflowID results in same Hash"` case), `responseCache.Fetch` returns workflow A's cached response to workflow B without ever contacting the external endpoint, as shown in `response_cache_test.go`'s `"returns cached response when cache hit"` test [9](#0-8) .

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L46-77)
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

// Fetch fetches a response from the cache if it exists and
// the age of cached response is less than the max age of the request.
// If the cached response is expired or not cached, it fetches a new response from the fetchFn
// and caches the response if it is cacheable and storeOnFetch is true.
//
// The mutex is only held during cache map access (microseconds), not during fetchFn execution.
// Singleflight deduplicates concurrent requests to the same cache key so only one fetchFn
// runs per key, while requests to different keys execute in parallel.
// Cache read and write happen inside the singleflight callback to ensure the key remains
// in-flight until the result is stored, preventing duplicate fetches.
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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L403-441)
```go
func (h *gatewayHandler) makeOutgoingRequest(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	requestID := resp.ID
	h.lggr.Debugw("handling outgoing message", "requestID", requestID, "nodeAddr", nodeAddr)
	var req gateway_common.OutboundHTTPRequest
	err := json.Unmarshal(*resp.Result, &req)
	if err != nil {
		return fmt.Errorf("failed to unmarshal HTTP request from node %s: %w", nodeAddr, err)
	}
	timeout := time.Duration(req.TimeoutMs) * time.Millisecond
	httpReq := network.HTTPRequest{
		Method:           req.Method,
		URL:              req.URL,
		Headers:          req.Headers, //nolint:staticcheck // forward deprecated Headers for backward compatibility; request uses MultiHeaders when set
		MultiHeaders:     req.MultiHeaders,
		Body:             req.Body,
		MaxResponseBytes: req.MaxResponseBytes,
		Timeout:          timeout,
	}

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
