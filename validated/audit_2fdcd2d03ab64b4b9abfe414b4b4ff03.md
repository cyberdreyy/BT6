Based on my investigation, I found a confirmed and directly-analogous bug: the gateway's HTTP response cache key is scoped by `WorkflowOwner`, not by `WorkflowID`, contrary to the documented design intent of per-workflow isolation.

### Title
Gateway response cache is scoped by workflow owner, not by workflow ID, allowing cross-workflow cached-response confusion - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The `responseCache` used by the HTTP capability gateway handler is documented and intended to provide "workflow-scoped caching" / "Workflow Isolation... to prevent cross-workflow data leakage," but the actual cache key (`OutboundHTTPRequest.Hash()`) intentionally ignores `WorkflowID` while including `WorkflowOwner`. This is the same class of accounting/scope mismatch as the reported `RewardsSystem.withdrawEarnings` bug: one part of the system computes a value over a broader set (all workflows owned by the same address) while another part of the system (the design/authorization intent) assumes isolation at a narrower granularity (per individual workflow), causing responses that belong to one workflow to be silently returned to a different, unrelated workflow.

### Finding Description
`responseCache.Fetch`/`Set` key entries using `req.Hash()`: [1](#0-0) [2](#0-1) 

The struct comment states the hash is derived from "method, URL, headers, body, workflowOwner": [3](#0-2) 

Unit tests explicitly confirm `WorkflowID` is excluded from the hash while `WorkflowOwner` is included: [4](#0-3) [5](#0-4) 

Yet the package's own README documents the opposite behavior/guarantee: [6](#0-5) 

The HTTP action handling path (`makeOutgoingRequest`) uses this cache to serve/store responses per node message without any additional workflow-ID-based partitioning: [7](#0-6) 

This mirrors the reported bug pattern in `RewardsSystem.withdrawEarnings`: the total/aggregate accounting (`empirePoints`, here the cache key computed over `workflowOwner`) includes a superset of entities (all of an owner's workflows) while the narrower per-entity guarantee (per-player distribution, here per-workflow isolation) is what callers/docs assume. Two different workflows belonging to the same owner that make an HTTP action request with identical method/URL/headers/body will collide on the same cache entry and can receive each other's previously-fetched HTTP response.

### Impact Explanation
Within a shared-owner multi-workflow deployment, one workflow can receive an HTTP response that was fetched and cached on behalf of a different workflow (cross-user/cross-workflow response confusion). If external endpoints return workflow/tenant-specific data based on request context outside the hashed fields (e.g., server-side session state, IP-based routing, or if two workflows are configured to hit the same URL/method/body but expect distinct external behavior), a workflow could act on stale or wrong data intended for a sibling workflow. This is a data-isolation violation directly contradicting the documented security property ("Workflow Isolation... to prevent cross-workflow data leakage").

### Likelihood Explanation
Likelihood is high whenever a single workflow owner runs multiple workflows that issue HTTP actions with `CacheSettings.Store=true`/`MaxAgeMs>0` to the same URL/method/headers/body — a plausible and unprivileged configuration reachable entirely by normal workflow authors (no special privilege needed), since caching is workflow-author-controlled via `CacheSettings` on the `OutboundHTTPRequest`.

### Recommendation
Include `WorkflowID` (or the full workflow execution scope) in the cache key computation used by `req.Hash()`, matching the documented "Cache Key: Generated from workflow ID and request hash" behavior, or explicitly reduce documentation claims to "owner-scoped" if per-workflow isolation is not actually a supported guarantee. Add a regression test asserting that two requests differing only in `WorkflowID` produce different cache entries (inverse of the existing test at lines 139-149).

### Proof of Concept
1. Workflow A (owner `0xOWNER`, workflowID `wf-A`) issues an `OutboundHTTPRequest{Method:"GET", URL:"https://api.example.com/data", WorkflowOwner:"0xOWNER", WorkflowID:"wf-A", CacheSettings:{Store:true, MaxAgeMs:600000}}`; response `R1` is cached under `req.Hash()`.
2. Workflow B (same owner `0xOWNER`, different workflowID `wf-B`) issues an identical request except `WorkflowID:"wf-B"`.
3. Per the test at [4](#0-3) , `req.Hash()` is identical for both, so Workflow B's `Fetch` call hits the cache entry populated by Workflow A and receives `R1` instead of issuing its own fresh request — demonstrating the cross-workflow response confusion.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L15-17)
```go
// responseCache is a thread-safe cache for storing HTTP responses.
// It uses a map to store responses keyed by a hash of the request (method, URL, headers, body, workflowOwner).
type responseCache struct {
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L151-174)
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
