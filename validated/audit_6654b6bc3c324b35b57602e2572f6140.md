### Title
Gateway HTTP Response Cache Key Omits WorkflowID, Causing Cross-Workflow Response Confusion - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The gateway's HTTP Handler V2 response cache is documented and intended to be scoped per-workflow ("Cache Key: Generated from workflow ID and request hash" / "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage"), but the actual cache key produced by `OutboundHTTPRequest.Hash()` does not include `WorkflowID`. A test explicitly documents this: `require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")` [1](#0-0) . This is analogous to the Venus `ChainlinkOracle` bug class: a value from an unintended/unverified source (a stale cache entry created for a *different* workflow) is served with priority over what should be a fresh, correctly-scoped fetch, because the mechanism that is supposed to gate/segregate the value (workflow identity) is not actually checked.

### Finding Description
`responseCache.Fetch` and `responseCache.Set` key the cache purely by `req.Hash()` [2](#0-1) [3](#0-2) . The dispatch path `makeOutgoingRequest` calls `h.responseCache.Fetch(httpCtx, req, callback, req.CacheSettings.Store)` whenever `req.CacheSettings.MaxAgeMs > 0` [4](#0-3) , where `req` is an `OutboundHTTPRequest` unmarshaled directly from the JSON-RPC message sent by a workflow-DON node acting on behalf of a workflow [5](#0-4) .

The component README explicitly states the cache should be workflow-scoped: "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [6](#0-5) . However, the unit test suite for `Hash()` proves the implementation contradicts this design intent — `WorkflowID` differences do not change the hash, while `WorkflowOwner` differences do [7](#0-6) . Because only method/URL/headers/body/workflowOwner feed into the hash (not `WorkflowID`), any two different workflows belonging to the same owner (or, if `WorkflowOwner` is also empty/absent, potentially unrelated workflows) that issue an outbound HTTP action with an identical method/URL/headers/body will collide on the same cache entry.

This mirrors the oracle bug class: a "manually set"/previously-cached value (created under one workflow's context/trust boundary) is returned in preference to a fresh, correctly-scoped fetch for a different, unrelated workflow — without any check that the value actually belongs to the requesting workflow, and it can persist and be replayed until TTL expiry regardless of whether the originating workflow is still valid or the request context has changed.

### Impact Explanation
Because the cache does not enforce per-`WorkflowID` isolation as documented, a workflow can receive an HTTP response that was actually generated for (and possibly contains data specific to) a different workflow sharing the same owner and same outbound request shape (URL/method/headers/body). This is a cross-user/cross-workflow response confusion bug: sensitive response bodies (which may include API responses containing tokens, personalized data, or state specific to one workflow's execution context) could leak into a sibling workflow's execution, or a workflow could be served attacker-influenced/stale data cached by another workflow, undermining the "Workflow Isolation" guarantee the system claims to provide.

### Likelihood Explanation
This is reachable purely from the standard, unprivileged HTTP Action capability request path used by any workflow node — no special privilege beyond normal workflow authoring is required. The only precondition is two workflows (under the same owner, or with equal/empty `WorkflowOwner`) issuing outbound HTTP requests with identical method/URL/headers/body and `CacheSettings.MaxAgeMs > 0`/`Store: true`, which is a routine caching configuration exposed to workflow authors, not an edge case.

### Recommendation
Include `WorkflowID` (and, if not already effectively enforced, `WorkflowOwner`) as part of the cache key input to `OutboundHTTPRequest.Hash()`, or otherwise incorporate the workflow identity into the map key inside `responseCache` (e.g., `map[workflowID]map[string]*cachedResponse` or a composite key `workflowID + "|" + req.Hash()`), so that cache entries cannot be shared across distinct workflows, consistent with the documented "Workflow Isolation" behavior.

### Proof of Concept
1. Workflow A (`WorkflowID: "wf-A"`, `WorkflowOwner: "0xOwner"`) issues an `OutboundHTTPRequest{Method: "GET", URL: "https://api.example.com/data", CacheSettings: {MaxAgeMs: 600000, Store: true}}` through the gateway's HTTP Action capability; the gateway fetches and caches the response under `req.Hash()` via `responseCache.Set`/`Fetch` [4](#0-3) .
2. Workflow B (`WorkflowID: "wf-B"`, same `WorkflowOwner: "0xOwner"`) issues the identical `OutboundHTTPRequest` (same method/URL/headers/body) with `CacheSettings.MaxAgeMs > 0`.
3. Because `Hash()` ignores `WorkflowID` (confirmed by `TestRequestHash`'s "having different workflowID results in same Hash" case [1](#0-0) ), Workflow B receives Workflow A's cached response from `rc.cache[cacheKey]` [8](#0-7)  without ever performing its own fetch, demonstrating cross-workflow response reuse in violation of the documented isolation guarantee.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L139-174)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L403-420)
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
