Based on my investigation, I found a bug-class match that is consistent with the report's pattern (a mismatch between documented/intended behavior and actual implementation causing stale/incorrect scoping of shared state), reachable from an unprivileged workflow node request to the internet-facing gateway.

### Title
Response cache key omits WorkflowID, causing cross-workflow cached HTTP response reuse contrary to documented isolation - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The gateway's `responseCache` is documented as being "scoped by workflow ID to prevent cross-workflow data leakage" [1](#0-0) , but the actual cache key, `req.Hash()`, does not incorporate `WorkflowID` at all — only `WorkflowOwner` (plus method/URL/headers/body) is included, as proven directly by the test suite.

### Finding Description
`responseCache.Fetch`/`Set`/`isExpiredOrNotCached` key the cache map by `req.Hash()` [2](#0-1) [3](#0-2) . The type comment even states the hash is derived from "method, URL, headers, body, workflowOwner" — `WorkflowID` is not mentioned [4](#0-3) . `TestRequestHash` explicitly asserts this: "having different workflowID results in same Hash" — `require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")` [5](#0-4) . Meanwhile the module's own README documents the opposite: "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [1](#0-0) .

This cache is populated and read directly from unprivileged workflow-node-originated HTTP action requests handled by `gatewayHandler.makeOutgoingRequest`, which calls `h.responseCache.Fetch`/`Set` using attacker/workflow-controlled `CacheSettings` fields [6](#0-5) .

### Impact Explanation
Because the cache key is scoped to `WorkflowOwner` rather than `WorkflowID`, any two different workflows belonging to the same owner (or a `WorkflowOwner` value that collides/matches another workflow's owner string) that issue outbound HTTP action requests with the same method/URL/headers/body will read each other's cached HTTP responses, contradicting the documented per-workflow isolation guarantee. This is a cross-workflow response confusion: one workflow can observe response data that was fetched/cached in the context of a different workflow (e.g., differing `MaxAgeMs`/`Store` semantics or dynamic responses tied to workflow-specific request state that isn't reflected in the hash), even though the requests are distinguishable at the `WorkflowID` granularity that the documentation promises. This does not, however, cross authentication/authorization boundaries between different owners, since `WorkflowOwner` is still part of the hash.

### Likelihood Explanation
Likelihood is limited: exploitation requires two workflows to share the exact same `WorkflowOwner`, HTTP method, URL, headers, and body, which is plausible for a single owner running multiple workflow versions/instances against the same external endpoint but does not constitute a cross-owner breach. This is more accurately classified as a caching-isolation defect than a severe unauthorized-access vulnerability, since the isolation boundary that's actually enforced is per-owner, not per-workflow as documented.

### Recommendation
Include `WorkflowID` in `OutboundHTTPRequest.Hash()` (or otherwise incorporate it into the cache key used in `Fetch`/`Set`/`isExpiredOrNotCached`) so that the implementation matches the documented workflow-scoped isolation guarantee, and update or remove the stale test assertion that currently locks in the opposite (undocumented) behavior.

### Proof of Concept
`response_cache_test.go` lines 139-149 already demonstrate the root cause directly: two requests with identical method/URL/owner but different `WorkflowID` produce an identical cache hash [5](#0-4) . Practically, Workflow A (owner `0xabc`, ID `wf-1`) issues a cacheable GET to `https://api.example.com/data` with `CacheSettings{Store:true, MaxAgeMs:600000}`; the response is stored under a key independent of `wf-1`. Workflow B (same owner `0xabc`, different ID `wf-2`) issuing the identical GET within the TTL window receives Workflow A's cached response via `gatewayHandler.makeOutgoingRequest` → `responseCache.Fetch` [7](#0-6) , without a fresh outbound HTTP call being made for `wf-2`.

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L15-16)
```go
// responseCache is a thread-safe cache for storing HTTP responses.
// It uses a map to store responses keyed by a hash of the request (method, URL, headers, body, workflowOwner).
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
