### Title
Cross-workflow HTTP response cache pollution due to WorkflowID exclusion from cache key - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The Gateway's HTTP Action response cache (`responseCache`) is keyed by a hash of the outbound HTTP request that intentionally excludes `WorkflowID`, while including `WorkflowOwner`. This mirrors the reported bug class: a downstream consumer (the cache, analogous to `LiquidityManager`) accepts and returns data (cached HTTP responses) without validating that it is scoped/expected for the specific requesting context (the individual workflow, analogous to a specific managed token), causing responses fetched for one workflow to be returned to a different, unrelated workflow that happens to share the same owner and target URL/method/body.

### Finding Description
`responseCache` documents itself as being "keyed by a hash of the request (method, URL, headers, body, workflowOwner)" [1](#0-0) , and its `Fetch`/`Set` methods use `req.Hash()` as the sole cache key without any additional scoping by `WorkflowID` [2](#0-1) .

Tests explicitly confirm the design: `TestRequestHash` asserts "having different workflowID results in same Hash" [3](#0-2)  while different `WorkflowOwner` values do change the hash [4](#0-3) .

This directly contradicts the component's own README, which states: "**Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [5](#0-4) . In practice, isolation is only by `WorkflowOwner`, not by `WorkflowID`.

The cache is populated and read directly from node-originated `HandleNodeMessage` calls without any per-workflow authorization check tying the cached entry back to the workflow that issued the specific request [6](#0-5) .

### Impact Explanation
If a single workflow owner runs multiple distinct workflows (or workflow versions) that call the same external URL/method/headers/body with caching enabled (`CacheSettings.MaxAgeMs > 0`), one workflow can receive an HTTP response that was actually fetched on behalf of a different workflow belonging to the same owner. This is a cross-workflow response confusion: sensitive or workflow-specific response content (e.g., data meant only for workflow A's external API call, potentially containing per-workflow tokens, session data, or personalized content returned by the same URL) could leak into workflow B's execution, and vice versa. This matches the report's "unlisted tokens returned to the wrong destination" class — the recipient trusts the cache to only ever return responses relevant to its own request context, but the cache silently serves cross-workflow content because workflow identity is not part of the cache key.

### Likelihood Explanation
The requirement for the same `WorkflowOwner` + identical method/URL/headers/body reduces exposure to other owners, but is easily met when: an owner deploys multiple workflows that call the same shared/common API endpoint (e.g., a company-wide price feed, shared internal service, or common integration used across several of their workflows) with caching enabled. This is a plausible and even common pattern (shared endpoints reused across workflow variants by the same team/owner), so likelihood of accidental cross-workflow leakage is moderate; it does not require any active attacker action beyond normal use, since it is triggered purely by two of the owner's own workflows hitting the same cached key.

### Recommendation
Include `WorkflowID` (or another workflow-execution-scoping identifier) as part of the `responseCache` hash key in `req.Hash()` / `OutboundHTTPRequest`, matching what the component's own README promises. Alternatively, if this is by design (e.g., allowing legitimate cross-workflow cache-sharing for the same owner), document this intentional behavior in the README, remove the misleading "Workflow Isolation ... scoped by workflow ID" claim, and ensure the CacheSettings API clearly communicates the actual isolation boundary (owner-level, not workflow-level) so workflow authors do not rely on false isolation guarantees when caching sensitive responses.

### Proof of Concept
1. Workflow A (owner `0xOwner1`, `WorkflowID = "wf-A"`) issues an HTTP Action to `GET https://api.example.com/data` with `CacheSettings{Store: true, MaxAgeMs: 600000}`. The gateway fetches and caches the response keyed by `Hash(method, URL, headers, body, workflowOwner="0xOwner1")` [7](#0-6) .
2. Workflow B, deployed by the same owner `0xOwner1` but with a different `WorkflowID = "wf-B"`, issues an identical `GET https://api.example.com/data` request with the same `CacheSettings`.
3. Because `WorkflowID` is excluded from `Hash()` (confirmed by `TestRequestHash` "having different workflowID results in same Hash" [3](#0-2) ), Workflow B's request hits the same cache entry and receives the response originally fetched for Workflow A — without any additional check that Workflow B was authorized to see that specific cached payload.

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

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L70-73)
```markdown
- **Cache Invalidation**: Time-based expiration with periodic cleanup
- **Cache Strategy**: All cacheable responses are cached; Non-zero `CacheSettings.MaxAgeMs` determines whether to return a cached value or make a fresh request
- **Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage
---
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
