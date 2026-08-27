Found a concrete match. The README explicitly documents that `responseCache` is supposed to provide "Workflow Isolation" — "Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [1](#0-0) . But the actual implementation and its own tests confirm the cache key does **not** include `WorkflowID`, only method/URL/headers/body/workflowOwner:

```go
// responseCache is a thread-safe cache for storing HTTP responses.
// It uses a map to store responses keyed by a hash of the request (method, URL, headers, body, workflowOwner).
``` [2](#0-1) 

And the test explicitly asserts this behavior as intended: `"having different workflowID results in same Hash"` [3](#0-2) .

### Title
Response cache key omits `WorkflowID`, enabling cross-workflow cached-response confusion despite documented workflow isolation - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The Ajna finding's root bug class is: a value (deposit time / validity marker) computed and validated in one context is carried over into a different context without re-validating it against that destination context's own isolation boundary, producing incorrect cross-context state. The `responseCache` in the Chainlink HTTP capability gateway handler exhibits the same class of bug: its cache key is derived from `OutboundHTTPRequest.Hash()`, which is documented and tested to exclude `WorkflowID` (only `workflowOwner` is included), so a cached HTTP response fetched on behalf of one workflow can be returned to a different, unrelated workflow that happens to issue a request with the same method/URL/headers/body and same owner (or, if owner also matches by coincidence/multi-tenant address reuse, cache entries cross workflow boundaries entirely).

### Finding Description
`Fetch()` and `Set()` in `responseCache` key cache entries purely on `req.Hash()`:
```go
func (rc *responseCache) Fetch(ctx context.Context, req gateway.OutboundHTTPRequest, fetchFn func() gateway.OutboundHTTPResponse, storeOnFetch bool) gateway.OutboundHTTPResponse {
	cacheKey := req.Hash()
	...
``` [4](#0-3) 

The package's own README states the design intent is workflow-scoped isolation: "Cache Key: Generated from workflow ID and request hash" and "Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage" [5](#0-4) . However the implementation's hash comment and the accompanying test explicitly confirm `WorkflowID` is NOT part of the hash — only `workflowOwner` differentiates entries:
```go
t.Run("having different workflowID results in same Hash", func(t *testing.T) {
    ...
    require.Equal(t, hash1, hash2, "Hash should be the same regardless of WorkflowID")
})
``` [3](#0-2) 

This is the same root cause pattern as `moveLiquidity()`: a piece of state (here, a cached HTTP response) that was validated/scoped for context A (workflow A, under owner O) is transparently reused in context B (workflow B, also under owner O, or any workflow sharing the same owner) without re-checking the destination context's isolation key (`WorkflowID`), because the code never carries `WorkflowID` forward into the comparison at all.

### Impact Explanation
Any workflow belonging to the same `workflowOwner` (an unprivileged actor operating multiple workflows, or workflows onboarded under a shared owner identity) that issues an HTTP action with the same method/URL/headers/body as a different workflow can receive that other workflow's previously cached response body — including any sensitive data (auth tokens, per-workflow secrets returned by an external endpoint, PII) embedded in that response — without making its own outbound request. This is a cross-workflow response confusion/data leak inside the internet-facing gateway's caching layer, directly matching the "cross-user response confusion" category called out in the validation rules.

### Likelihood Explanation
Likelihood is moderate-to-high in any deployment where a single `workflowOwner` runs multiple distinct workflows (a common and expected scenario) that hit the same external URL with `CacheSettings.Store = true` and `MaxAgeMs > 0`. No malicious behavior beyond normal workflow configuration is required — the confusion happens automatically as an unintended side effect of the missing `WorkflowID` scoping, contradicting the documented design guarantee.

### Recommendation
Include `WorkflowID` (not just `workflowOwner`) as part of the cache key computed by `OutboundHTTPRequest.Hash()`, matching the documented behavior in the README, so responses cached for one workflow are never served to a different workflow even when they share an owner and issue byte-identical requests.

### Proof of Concept
1. Workflow A (owner `O`) issues an `OutboundHTTPRequest` to `https://api.example.com/data` with `CacheSettings{Store: true, MaxAgeMs: 600000}`; the gateway fetches and caches the response under `req.Hash()` (no `WorkflowID` component) as shown in `Fetch`/`Set` [6](#0-5) .
2. Workflow B, also under owner `O` (or any workflow producing the same hash-relevant fields), issues an identical `OutboundHTTPRequest` (same method/URL/headers/body/owner, different `WorkflowID`) with `MaxAgeMs > 0`.
3. Because `Hash()` ignores `WorkflowID` (confirmed by `TestRequestHash`'s `"having different workflowID results in same Hash"` subtest [3](#0-2) ), `Fetch()` returns Workflow A's cached response to Workflow B without any new outbound HTTP call, violating the documented workflow isolation guarantee.

### Citations

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
