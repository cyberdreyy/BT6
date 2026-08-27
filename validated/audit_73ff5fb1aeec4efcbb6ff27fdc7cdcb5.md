### Title
Response Cache Key Ignores WorkflowID, Enabling Cross-Workflow HTTP Response Confusion - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The gateway's HTTP Action response cache is documented as scoping cache entries by `WorkflowID` to prevent cross-workflow data leakage, but the actual `Hash()` key used to store/retrieve cached responses does not include `WorkflowID` at all — confirmed by unit tests that explicitly assert identical hashes for different `WorkflowID` values.

### Finding Description
Similar to the RubiconRouter bug, where a function trusted a caller-controlled parameter (`targetPool`) to determine access to shared state (the contract's WETH balance) without independently verifying the caller's actual entitlement, the response cache here keys shared, mutable state (a cache of previously-fetched HTTP responses) on attacker/workflow-supplied fields that do not uniquely bind the request to a specific workflow.

The `responseCache` is documented as: "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [1](#0-0) . However, the code comment on the struct itself states the key is derived from "method, URL, headers, body, workflowOwner" — `WorkflowID` is not part of it [2](#0-1) . The `Fetch` and `Set` methods use `req.Hash()` directly as the map key with no additional per-workflow-ID scoping [3](#0-2) .

This is confirmed by `TestRequestHash` in the test suite, which explicitly verifies: "having different workflowID results in same Hash" — i.e., two requests differing only in `WorkflowID` produce an identical cache key [4](#0-3) . Only `WorkflowOwner` differentiates the hash [5](#0-4) .

The cache is populated/read from `makeOutgoingRequest`, which is invoked per node HTTP Action request and uses `req.CacheSettings.MaxAgeMs`/`Store` (both attacker/workflow-controlled fields embedded in the outbound HTTP action request) to decide caching behavior [6](#0-5) .

### Impact Explanation
Because the cache key omits `WorkflowID`, two different workflows belonging to the **same workflow owner** (e.g., an account that runs multiple distinct CRE workflows) that issue an HTTP Action with the same method/URL/headers/body will read and write to the same cache entry. If one workflow's HTTP request is designed to differ semantically only via metadata not captured in the hash (e.g., relying on `WorkflowID`-scoped server-side behavior, or if two workflows under one owner legitimately hit the same URL but expect isolated caching per workflow), a response fetched for one workflow can be served to another workflow within the TTL window. This is a concrete instance of the "cross-user response confusion" class explicitly permitted by the validation rules, since responses (potentially containing workflow-specific or sensitive data returned by the external endpoint) can leak across workflow boundaries that the system's own documentation promises to isolate.

### Likelihood Explanation
Likelihood is moderate: exploitation requires two workflows under the same authenticated owner to issue outbound HTTP Action requests with identical method/URL/headers/body and non-zero `CacheSettings.MaxAgeMs`/`Store` fields, which is fully controllable by the unprivileged workflow author submitting the CRE workflow (no privileged access needed) — this mirrors the analog report's pattern where an attacker fully controls a parameter (there, `targetPool`; here, `CacheSettings` and request fields) to manipulate shared state that the target contract/service relies on for authorization/scoping decisions.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`) as part of the `OutboundHTTPRequest.Hash()` computation used as the response cache key, so that the code matches the documented workflow-isolation guarantee, or explicitly document that isolation is only owner-scoped (not workflow-scoped) if that is the intended design, and adjust the README language in `core/services/gateway/handlers/capabilities/v2/README.md` accordingly to avoid a documented-vs-implemented security guarantee mismatch.

### Proof of Concept
1. Workflow A (owner `0xabc`, `WorkflowID = "wf-A"`) issues an HTTP Action `GET https://example.com/api` with `CacheSettings.Store = true` and receives/caches a response.
2. Workflow B, owned by the same `0xabc` account but with a different `WorkflowID = "wf-B"`, issues the identical `GET https://example.com/api` request with `CacheSettings.MaxAgeMs > 0`.
3. Per `Hash()`/`Fetch()` logic, since `WorkflowID` is excluded from the key, Workflow B receives Workflow A's cached response without a fresh outbound request being made, verified directly by the test `TestRequestHash`/"having different workflowID results in same Hash" [4](#0-3) .

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
