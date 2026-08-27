## Finding: Response cache in the CRE HTTP Gateway is keyed by `workflowOwner`, not `workflowID`, despite documented workflow-level isolation

This is a solid analog to the Masset bug class: an internal invariant ("cache entries are scoped by workflow ID to prevent cross-workflow data leakage") that the code does not actually enforce, causing state (a cached HTTP response) to diverge from what callers are entitled to see. In Masset, the accounting divergence was between vault-balance bookkeeping and actual lending-pool balance; here it's between the documented/expected cache-isolation key (workflow ID) and the actual cache key (workflow owner + request), letting responses cross workflow boundaries.

### Root cause

The `responseCache` in the gateway's HTTP capability handler explicitly documents that it keys entries "by a hash of the request (method, URL, headers, body, workflowOwner)" — it does not include `WorkflowID`: [1](#0-0) 

But the package README states the opposite guarantee — that the cache key is generated from workflow ID and that isolation is by workflow ID specifically to prevent cross-workflow leakage: [2](#0-1) 

The unit tests confirm the actual (undocumented) behavior: two requests with different `WorkflowID` but the same owner/method/URL/headers/body produce the **same** hash/cache key, while two requests with different `WorkflowOwner` produce different keys: [3](#0-2) 

This key is used directly in `Fetch`/`Set` to read and write the shared in-memory cache map that serves all workflows connected through the gateway: [4](#0-3) 

And it's reached from the gateway's outbound-HTTP-action path, which is driven by requests originating from workflow (capability) nodes over the gateway connector — i.e., the internet-facing path handling per-workflow outbound HTTP actions: [5](#0-4) 

### Impact

Any two workflows deployed by the same `WorkflowOwner` that happen to issue HTTP action requests with identical method/URL/headers/body (a very plausible collision for commonly-used integrations/APIs) will read and write the **same cache entry**, regardless of `WorkflowID`. A workflow with `CacheSettings.Store=true` can therefore:
- Read a cached response that was fetched/authorized in the context of a *different* workflow (cross-workflow response confusion), or
- Poison the cache for a sibling workflow (same owner, different `WorkflowID`) by triggering a request with attacker-influenced body/headers, causing that sibling workflow to later consume a manipulated cached response instead of a request it made itself.

This directly contradicts the "Workflow Isolation" guarantee documented for the feature and can leak or corrupt data across workflow boundaries under the same owner, without any privileged access — one workflow submission is enough to affect another workflow's outbound HTTP capability behavior.

### Recommendation

Include `WorkflowID` in the cache-key hash (in addition to method/URL/headers/body/owner) so that cache entries are actually scoped per workflow, matching the documented invariant, and update/extend the test suite to assert that different `WorkflowID`s produce different hashes. [6](#0-5)

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L94-120)
```go
func TestRequestHash(t *testing.T) {
	req := createTestRequest("GET", "https://example.com")

	t.Run("generates consistent hash", func(t *testing.T) {
		hash1 := req.Hash()
		hash2 := req.Hash()
		require.Equal(t, hash1, hash2)
	})

	t.Run("different requests generate different hashes", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com/path1")
		req2 := createTestRequest("GET", "https://example.com/path2")

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.NotEqual(t, hash1, hash2)
	})

	t.Run("same request with different method generates different hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req2 := createTestRequest("POST", "https://example.com")

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.NotEqual(t, hash1, hash2)
	})

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
