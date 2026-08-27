This is the key finding: `Hash()` on `OutboundHTTPRequest` explicitly excludes `WorkflowID` from the cache key, as proven directly by the test `TestRequestHash`'s subtest "having different workflowID results in same Hash" [1](#0-0) . Yet `WorkflowOwner` **is** part of the hash and does differentiate entries [2](#0-1) . This directly contradicts the documented design intent that "Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [3](#0-2) .

The `responseCache.Fetch`/`Set` implementation keys strictly off `req.Hash()` and stores/returns the full cached `OutboundHTTPResponse` (headers, body) to any caller presenting a request that hashes identically [4](#0-3) . Since `WorkflowID` is dropped from the digest but `WorkflowOwner` is not, two different workflows belonging to the **same owner** (a common, unprivileged multi-workflow scenario) that issue an outbound HTTP action with the same method/URL/headers/body/cache settings will collide on the same cache entry, causing one workflow's fetched response to be silently served to a different, unrelated workflow. This is directly reachable from an unprivileged workflow author's own capability call (`makeOutgoingRequest` → `createHTTPRequestCallback` → `responseCache.Fetch`) [5](#0-4) , requiring no privileged access and no malicious node/peer — it is a genuine cross-workflow (cross-user, in the sense of cross-execution-context) response confusion bug analogous to the reentrancy-driven fund/response misdelivery in the reference report (an unprivileged actor's inputs cause a shared internal resource — the cache/wrapped-token balance — to leak state to another logical consumer).

### Title
Workflow-ID omission from `OutboundHTTPRequest.Hash()` causes cross-workflow HTTP response cache poisoning/leakage - (File: `core/services/gateway/handlers/capabilities/v2/response_cache.go`)

### Summary
The gateway's outbound HTTP action response cache is documented as being scoped per-workflow to prevent cross-workflow leakage, but the cache key (`req.Hash()`) explicitly excludes `WorkflowID` while including `WorkflowOwner`. As a result, distinct workflows owned by the same owner that make byte-identical outbound HTTP requests share a single cache entry.

### Finding Description
`gatewayHandler.makeOutgoingRequest` builds a cache-backed callback and, when `CacheSettings.MaxAgeMs > 0`, calls `h.responseCache.Fetch(httpCtx, req, callback, req.CacheSettings.Store)` [6](#0-5) . `Fetch` computes `cacheKey := req.Hash()` and reads/writes the shared `rc.cache` map keyed solely by that hash [7](#0-6) . The unit test suite confirms the hash intentionally ignores `WorkflowID`, while it does incorporate `WorkflowOwner` [8](#0-7) . The design documentation claims "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [9](#0-8) , which the implementation does not enforce.

### Impact Explanation
Any workflow owner running multiple workflows can trigger one workflow to read the cached HTTP response that was originally fetched (and possibly rate-limited, dynamically generated, or containing sensitive per-workflow tokens/headers) for a different workflow, as long as method/URL/headers/body/cache settings match. This is a cross-workflow response confusion: data intended for workflow A's outbound HTTP action is served to workflow B without re-fetching, undermining workflow isolation guarantees and potentially leaking secrets or stale/incorrect data used in downstream on-chain logic.

### Likelihood Explanation
Likelihood is moderate: exploitation only requires an unprivileged workflow owner to run two (or more) workflows that happen to issue requests with identical method, URL, headers, body, and non-zero `CacheSettings.MaxAgeMs` — a realistic occurrence for shared/templated workflow logic, and entirely reachable through the normal, unprivileged HTTP action capability path without needing a malicious node or peer.

### Recommendation
Include `WorkflowID` (and/or `WorkflowOwner`) in the cache key computation used by `Hash()`/`responseCache`, or otherwise namespace the `rc.cache` map by workflow ID so responses cannot be shared across workflows, matching the documented isolation guarantee.

### Proof of Concept
1. Workflow A (owner `0xabc`, workflow ID `wf-1`) issues an `OutboundHTTPRequest{Method:"GET", URL:"https://example.com", CacheSettings:{Store:true, MaxAgeMs:600000}}`; the gateway fetches it, then caches it under `req.Hash()` via `Fetch`/`Set` [10](#0-9) .
2. Workflow B (same owner `0xabc`, different workflow ID `wf-2`) issues an identical `OutboundHTTPRequest` (same method/URL/headers/body/cache settings, different `WorkflowID`).
3. Because `Hash()` ignores `WorkflowID`, `Fetch` finds the pre-existing entry keyed by the same hash and returns Workflow A's cached response to Workflow B without making a new HTTP request, as verified directly by the test asserting identical hashes across differing `WorkflowID` values [1](#0-0) .

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-108)
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
