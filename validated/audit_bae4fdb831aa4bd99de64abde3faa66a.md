### Title
Response cache key omits `WorkflowID`, causing cross-workflow response confusion in the HTTP capability gateway handler - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The gateway's `responseCache` used for the HTTP capability's outbound-action caching computes its cache key from `gateway_common.OutboundHTTPRequest.Hash()`, which explicitly excludes `WorkflowID` while including `WorkflowOwner`. This mirrors the reported DNS "version mismatch" bug class: a versioning/scoping identifier that should disambiguate independently-owned contexts (there, the zone version; here, the workflow ID) is dropped when computing the storage/lookup key, so a lookup performed under one context (workflow) can be satisfied with data written under a different context (a different workflow of the same owner).

### Finding Description
`responseCache.Fetch` and `responseCache.Set` key cached HTTP responses using `req.Hash()`: [1](#0-0) [2](#0-1) 

The comment on the struct states the hash is derived from "method, URL, headers, body, workflowOwner" — `WorkflowID` is not part of the key: [3](#0-2) 

This is confirmed by the test suite itself, which explicitly asserts that requests with different `WorkflowID` values produce the *same* hash, while requests with different `WorkflowOwner` values produce different hashes: [4](#0-3) [5](#0-4) 

However, the component's own README documents the intended design as workflow-scoped isolation to prevent cross-workflow leakage: [6](#0-5) 

The cache is populated/consulted in `makeOutgoingRequest`, which is invoked for every outbound HTTP action forwarded by a node on behalf of a workflow, using whatever `WorkflowID`/`WorkflowOwner`/cache settings were embedded in the (workflow-controlled) `OutboundHTTPRequest`: [7](#0-6) 

Because `WorkflowID` is not part of the cache key, two distinct workflows belonging to the same owner (or any two requests with the same owner, method, URL, headers and body) collapse to the same cache entry. A response fetched/cached for workflow A's request (e.g. containing account-specific data returned by the target endpoint, or a stale 4xx/2xx result appropriate only to workflow A's calling pattern) can be transparently served to workflow B simply because both share `WorkflowOwner` and produce an identical `Hash()`. This is analogous to the DNS resolver bug where a lookup keyed by the wrong "version" (zone version instead of host version) returns data associated with a different logical entity than the one being queried.

### Impact Explanation
An unprivileged CRE user who owns multiple workflows (or whose workflow issues requests that happen to match another of their workflows' request signature) can have cache-stored HTTP responses served across workflow boundaries, defeating the intended per-workflow isolation guarantee described in the README. Depending on what the target endpoint returns (e.g., time-sensitive tokens, per-workflow state, or endpoint responses gated by request timing/side channels), this can lead to cross-workflow response confusion — a workflow silently consuming stale or logically-mismatched data intended for a different workflow context, undermining the correctness/isolation guarantees of the gateway's caching layer.

### Likelihood Explanation
Likelihood is moderate: it requires only that a workflow owner run two (or more) workflows that issue outbound HTTP actions with identical method/URL/headers/body and use `CacheSettings.Store`/`MaxAgeMs` — a realistic and easily reproducible configuration for common integrations (e.g., polling the same public API endpoint from multiple workflows). No privilege escalation, malicious peer, or network-layer condition is required; it is reachable purely through normal workflow authoring by an unprivileged DON/workflow owner.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`) in the `Hash()` computation used as the cache key, or otherwise scope the `responseCache` map by workflow ID, so that responses cached under one workflow's context cannot be returned to a different workflow's fetch, matching the documented "workflow-scoped caching" intent.

### Proof of Concept
1. Workflow A (owner `0xAbC`, `WorkflowID = "wf-A"`) issues an `OutboundHTTPRequest{Method: "GET", URL: "https://api.example.com/data", CacheSettings: {Store: true, MaxAgeMs: 600000}}`; the gateway calls `responseCache.Fetch`, misses, executes the HTTP call, and stores the result under `req.Hash()` (which does not include `wf-A`).
2. Workflow B (same owner `0xAbC`, different `WorkflowID = "wf-B"`) issues an identical `OutboundHTTPRequest` (same method/URL/headers/body) with the same `CacheSettings`.
3. Because `Hash()` ignores `WorkflowID`, `responseCache.Fetch` for workflow B's request computes the same key as workflow A's and returns workflow A's cached response without making a new HTTP request — demonstrated directly by the existing unit test asserting equal hashes for differing `WorkflowID` values: [4](#0-3) .

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
