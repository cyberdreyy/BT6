## Title
Cache key hash omits `WorkflowID`, allowing cross-workflow response cache overwrite/poisoning in the Gateway HTTP capability handler - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

## Summary
The Sherlock finding describes a mapping (`bulls`/`bears`/`matchOrders`) keyed by a hash (`contractId`) that omits enough distinguishing fields, letting an attacker craft a colliding hash and overwrite another party's stored value. The analogous pattern in this codebase is the Gateway's HTTP-action `responseCache`, whose cache key is a hash of the outbound HTTP request that intentionally excludes `WorkflowID` (and `CacheSettings`), even though the component's own documentation claims workflow-level isolation.

## Finding Description
`responseCache` stores cached `OutboundHTTPResponse` values in a map keyed by `req.Hash()` [1](#0-0) . The `Fetch` and `Set` methods use this hash directly as the map key when reading and writing cache entries [2](#0-1) [3](#0-2) .

The test suite confirms that `Hash()` deliberately produces identical output for requests with different `WorkflowID` values, while only `workflowOwner` differentiates the hash: [4](#0-3) [5](#0-4) .

This directly contradicts the component's own README, which states caching is workflow-scoped and isolated to prevent cross-workflow data leakage: [6](#0-5) .

The cache is populated and consumed from `makeOutgoingRequest`, which is invoked per node message for HTTP-action requests originating from workflow nodes: [7](#0-6) . Because the key omits `WorkflowID`, any two workflows belonging to the same `workflowOwner` that issue a request with the same method/URL/headers/body will share a single cache slot — a response fetched (and cached) on behalf of one workflow can be returned to, or overwritten by, a completely different workflow, regardless of each workflow's own `CacheSettings.Store`/`MaxAgeMs` intent (which is also excluded from the hash, per [8](#0-7) ).

## Impact Explanation
An unprivileged workflow author (any user can create/register workflows against the gateway) can construct an HTTP-action request with `Store: true` and craft the URL/method/headers/body to intentionally collide with another workflow's known outbound request. Once the attacker's fabricated response is cached, any other workflow (same owner, or scoped to the same owner identity used in DON operation) making a request with the same hash will silently receive the attacker-controlled, stale, or overwritten data instead of the fresh external response — a cross-workflow response/cache poisoning that undermines the documented workflow isolation guarantee. This can lead to incorrect data being fed into downstream workflow computation/decisions.

## Likelihood Explanation
Reaching this path only requires an entity able to submit HTTP-action requests through the gateway (a workflow node acting on behalf of a workflow owner) with attacker-controlled `CacheSettings.Store=true` and a request matching another workflow's expected method/URL/headers/body — no special privilege beyond normal workflow execution is needed, and the behavior is exercised directly by `makeOutgoingRequest`/`responseCache.Fetch`/`Set`.

## Recommendation
Include `WorkflowID` (and ideally `CacheSettings` where semantically relevant) in the cache key hash computed by `OutboundHTTPRequest.Hash()`, or otherwise scope the `responseCache` map per `(workflowOwner, WorkflowID)` pair before hashing the request contents, so that cache entries cannot be shared/overwritten across distinct workflows. Align the implementation with the documented behavior in the README.

## Proof of Concept
1. Workflow A (owner `X`, `WorkflowID = "wf-A"`) issues an `OutboundHTTPRequest{Method:"GET", URL:"https://api.example.com/data", CacheSettings:{Store:true, MaxAgeMs:600000}}`.
2. The gateway calls `responseCache.Fetch`, which computes `cacheKey := req.Hash()` (excludes `WorkflowID`) and caches the legitimate response under that key [9](#0-8) .
3. Workflow B (same owner `X`, different `WorkflowID = "wf-B"`, potentially attacker-controlled) issues an identical `Method`/`URL`/`Headers`/`Body` request but is served first with `Store:true`, causing its (possibly manipulated) response to overwrite the cache entry for Workflow A once Workflow A's cache TTL/MaxAge triggers a refetch, or Workflow B is served the cache written by Workflow A before Workflow A ever expected sharing.
4. As demonstrated by `TestRequestHash`'s "having different workflowID results in same Hash" case [4](#0-3) , both requests collide to the same cache slot despite being logically distinct workflows.

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L111-120)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L121-137)
```go
	t.Run("having different cacheSettings results in the same Hash", func(t *testing.T) {
		req1 := createTestRequest("GET", "https://example.com")
		req1.CacheSettings = gateway_common.CacheSettings{
			MaxAgeMs: 5000,
			Store:    true,
		}

		req2 := createTestRequest("GET", "https://example.com")
		req2.CacheSettings = gateway_common.CacheSettings{
			MaxAgeMs: 10000,
			Store:    false,
		}

		hash1 := req1.Hash()
		hash2 := req2.Hash()
		require.Equal(t, hash1, hash2, "Hash should be the same regardless of CacheSettings")
	})
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
