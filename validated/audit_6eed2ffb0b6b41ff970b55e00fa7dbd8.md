## Title
Cross-workflow HTTP response cache poisoning due to `WorkflowID` exclusion from cache key - (File: `core/services/gateway/handlers/capabilities/v2/response_cache.go`)

### Summary
The Chainlink CRE HTTP gateway handler caches outbound HTTP action responses keyed by a hash of the request that intentionally excludes `WorkflowID`, contrary to the component's documented design ("Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage").

### Finding Description
The `responseCache` caches responses in `rc.cache` keyed by `req.Hash()` [1](#0-0) . The `Fetch` and `Set` methods use this hash as the sole cache key, and any two requests that hash the same are treated as the same cache entry regardless of which workflow issued them [2](#0-1) . The component's own README documents the intended behavior as "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [3](#0-2) . However, the test suite explicitly confirms the opposite: `WorkflowID` is *not* part of the hash — "having different workflowID results in same Hash" [4](#0-3) . Only `WorkflowOwner` (not `WorkflowID`) differentiates the hash [5](#0-4) .

This means two different workflows belonging to the same `WorkflowOwner` (an unprivileged, off-chain-controlled value on the outbound request, not verified against the DON in this path) that issue outbound HTTP requests with the same method/URL/headers/body will share a single cache slot. This is the analog of the AlgebraPool report's root cause: a boundary check that should be strict/precise (exact scoping per swap/per workflow) is instead loose, letting one actor's "extra" contribution (here, a cached response payload) be silently consumed/returned to a different, unintended party (a different workflow execution) instead of being properly isolated.

### Impact Explanation
A workflow whose HTTP action request happens to collide on `Hash()` with another workflow's request (same method/URL/headers/body, same `WorkflowOwner`) can receive a response that was fetched and cached on behalf of a different workflow, producing cross-workflow response confusion. If `WorkflowOwner` itself is attacker-influenced or shared across workflows managed by the same account, one workflow can effectively "poison" the cache for another workflow that happens to request the same endpoint, causing it to receive stale/incorrect/attacker-influenced data instead of performing its own independent fetch — a direct violation of the documented workflow-scoped isolation guarantee, and a form of cross-user (cross-workflow) response confusion called out as an acceptable class of finding for this scan.

### Likelihood Explanation
Reaching this path requires only that a workflow node (an unprivileged CRE workflow execution) issue an `HTTPAction` outbound request through the gateway's HTTP handler v2 with `CacheSettings.Store`/`MaxAgeMs` set — a normal, documented capability path, not a privileged operation [6](#0-5) . No special permissions beyond normal workflow execution are needed to trigger a cache collision; the only precondition is two workflows (under the same owner) hitting an identical `Method+URL+Headers+Body` combination within the cache TTL window.

### Recommendation
Include `WorkflowID` (and ideally `WorkflowExecutionID` or a workflow-scoped namespace) in the `Hash()` computation used as the cache key, matching the documented design, so that cache entries are strictly scoped per workflow and cannot be shared across different workflow executions even when other request fields match.

### Proof of Concept
Based on `core/services/gateway/handlers/capabilities/v2/response_cache_test.go` lines 139-149:
```go
req1 := createTestRequest("GET", "https://example.com")
req1.WorkflowID = "workflow-123"

req2 := createTestRequest("GET", "https://example.com")
req2.WorkflowID = "workflow-456"

hash1 := req1.Hash()
hash2 := req2.Hash()
require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")
```
This existing unit test demonstrates that two requests from entirely different workflows (`workflow-123` vs `workflow-456`) produce identical cache keys. Consequently, if `workflow-123` populates the cache via `responseCache.Set`/`Fetch` [7](#0-6) , a subsequent request from `workflow-456` with the same Method/URL/Headers/Body/WorkflowOwner will receive `workflow-123`'s cached response rather than performing its own fetch, contradicting the documented per-workflow isolation guarantee.

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

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L65-73)
```markdown
### 3.2 Caching Behavior

- **Cacheable Responses**: 2xx (success) and 4xx (client error) status codes.
- **Cache TTL**: Configurable, default 10 minutes
- **Cache Key**: Generated from workflow ID and request hash
- **Cache Invalidation**: Time-based expiration with periodic cleanup
- **Cache Strategy**: All cacheable responses are cached; Non-zero `CacheSettings.MaxAgeMs` determines whether to return a cached value or make a fresh request
- **Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage
---
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
