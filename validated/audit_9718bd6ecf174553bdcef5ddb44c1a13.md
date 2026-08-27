Based on the evidence, there is a valid analog: the HTTP gateway's response cache computes its cache key via `OutboundHTTPRequest.Hash()`, and that hash function deliberately/incorrectly excludes the `WorkflowID` field while including `WorkflowOwner`. This is directly analogous to the Tempus bug class — a value used to key/attribute a cached result is computed from the wrong/insufficient inputs, causing responses to be attributed to (and served to) the wrong caller (cross-workflow instead of the correct scope), despite the module's own documentation claiming per-workflow isolation.

### Title
Gateway HTTP response cache key omits `WorkflowID`, allowing cross-workflow cache poisoning/leakage - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The V2 HTTP capability gateway handler caches outbound HTTP responses keyed by `req.Hash()` [1](#0-0) . The handler's own documentation states caching is "Workflow-scoped" and that "Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [2](#0-1) . However, the test suite explicitly documents and asserts that two requests with different `WorkflowID` values produce the **same** hash: `require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")` [3](#0-2) . This is the same bug class as the Tempus `lend` finding: a derived value (there, the minted amount; here, the cache key) is computed from inputs that are unrelated/insufficient to the entity it's supposed to represent, and the documented invariant (workflow isolation / correct token amount) silently does not hold.

### Finding Description
`responseCache.Fetch` and `responseCache.Set` use `req.Hash()` as the sole cache key [4](#0-3) . Because `Hash()` does not incorporate `WorkflowID` (confirmed by the test at lines 139-149), any two workflows (even under different owners is untested for `WorkflowID` alone — the tests show `WorkflowOwner` differences do change the hash at lines 163-175, but `WorkflowID` differences do not) that issue an `OutboundHTTPRequest` with the same method/URL/body/headers will collide on the same cache entry. `makeOutgoingRequest` uses this cache transparently for any node request routed through the gateway when `CacheSettings.MaxAgeMs > 0` [5](#0-4) .

### Impact Explanation
An unprivileged workflow/user that can trigger an `OutboundHTTPRequest` (HTTP Action capability) with `CacheSettings.MaxAgeMs > 0` can receive a cached HTTP response that was actually fetched for a different workflow with the same `WorkflowID` but potentially different trust context, or can poison the cache for other workflows using the same method/URL. This breaks the workflow isolation guarantee the module claims and constitutes cross-user response confusion via the internet-facing gateway's caching layer.

### Likelihood Explanation
This is reachable directly from an unprivileged workflow request through `HandleNodeMessage` → `makeOutgoingRequest` → `responseCache.Fetch`, requiring no special privilege beyond issuing an HTTP action request with cache settings enabled, and requires no malicious node or peer — only a normal DON/workflow request path [6](#0-5) .

### Recommendation
Include `WorkflowID` (and confirm `WorkflowOwner`) as part of the cache key computation in the request's `Hash()` method so cache entries cannot collide across workflows, consistent with the documented "Workflow Isolation" behavior.

### Proof of Concept
The existing unit test itself demonstrates the flaw (it currently asserts the vulnerable behavior as expected): [3](#0-2) 

**Note on limitations:** the actual `Hash()` method implementation (likely in a `gateway_common`/`OutboundHTTPRequest` type file) could not be located via the available index/search tools within this session, so the exact fields hashed cannot be fully enumerated beyond what the test file demonstrates. If deeper verification of the `Hash()` implementation is required, a full-repository session (e.g., a Devin session) would be needed to inspect the file directly, since index size limits may exclude it from search results.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L46-72)
```markdown
#### 2.1.4 Response Cache (`responseCache`)
- **Purpose**: Caches HTTP responses to avoid redundant outbound requests
- **Functions**: TTL-based caching that optionally returns cached values based on max age parameter
- **Key Features**: Workflow-scoped caching

---

## 3. HTTP Action Message Handling

### 3.1 Process Flow

1. **Request Reception**: Gateway receives HTTP action request from a workflow node
2. **Rate Limiting**: Validates node rate limits
3. **Request Parsing**: Extracts `OutboundHTTPRequest` from the JSON-RPC message
4. **Cache Check**: Determines if request should use cached response or fetch fresh data
5. **HTTP Execution**: Makes actual HTTP request to external endpoint
6. **Response Caching**: Stores cacheable responses (2xx, 4xx status codes) only if `CacheSettings.Store` is `true`
7. **Node Response**: Sends HTTP response back to requesting node

### 3.2 Caching Behavior

- **Cacheable Responses**: 2xx (success) and 4xx (client error) status codes.
- **Cache TTL**: Configurable, default 10 minutes
- **Cache Key**: Generated from workflow ID and request hash
- **Cache Invalidation**: Time-based expiration with periodic cleanup
- **Cache Strategy**: All cacheable responses are cached; Non-zero `CacheSettings.MaxAgeMs` determines whether to return a cached value or make a fresh request
- **Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage
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
