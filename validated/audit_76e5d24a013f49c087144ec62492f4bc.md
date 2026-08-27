### Title
Cache poisoning across different workflows sharing the same `WorkflowOwner` due to `Hash()` excluding `WorkflowID` - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The response cache key is derived from `OutboundHTTPRequest.Hash()`, which explicitly excludes `WorkflowID` and only includes `WorkflowOwner` (plus Method/URL/Headers/Body). This is confirmed directly by the test suite, which asserts "having different workflowID results in same Hash" and "having same workflowOwner results in the same Hash." Consequently, any two workflows sharing the same `WorkflowOwner` value share the same cache entries, contradicting the component's documented "Workflow Isolation" guarantee ("Cache entries are scoped by workflow ID to prevent cross-workflow data leakage").

### Finding Description
`gatewayHandler.makeOutgoingRequest` (`core/services/gateway/handlers/capabilities/v2/http_handler.go:403-454`) unmarshals an `OutboundHTTPRequest` from a node message and, when `CacheSettings.Store` is true, calls `h.responseCache.Set(req, outboundResp)` or `h.responseCache.Fetch(ctx, req, callback, req.CacheSettings.Store)`.

Both `Set` and `Fetch` in `response_cache.go` key the shared, single map (`rc.cache map[string]*cachedResponse`) using `req.Hash()`: [1](#0-0) [2](#0-1) 

The test file directly documents and verifies the actual hash-key composition: it is stable across differing `WorkflowID` values but differs by `WorkflowOwner`: [3](#0-2) [4](#0-3) 

This means the cache is isolated per `WorkflowOwner`, not per `WorkflowID`, despite the package's own `README.md` claiming per-workflow-ID isolation: [5](#0-4) 

Exploit flow: an attacker who controls one workflow (workflow A) owned by owner `O` can send an `OutboundHTTPRequest` with `Method`/`URL`/`Headers`/`Body` matching what a *different* workflow (workflow B), also owned by `O` (e.g., a second version/deployment or another workflow the same customer/organization owns), is expected to request, with `CacheSettings.Store=true` and a forged response body. Because `Hash()` ignores `WorkflowID`, workflow B's subsequent `Fetch` with `MaxAgeMs>0` for the "same" request (same Method/URL/Headers/Body, same owner) will retrieve the entry poisoned by workflow A, receiving fabricated data instead of a fresh response from the real HTTP endpoint.

However, isolation *is* enforced by `WorkflowOwner` — cross-owner poisoning is not possible since different owners produce different hashes. The exploitable case is limited to workflows sharing the same `WorkflowOwner`.

### Impact Explanation
This is cross-workflow response confusion within the same owner's account: a compromised or malicious workflow can inject attacker-controlled HTTP response bodies/headers/status into a sibling workflow's HTTP action result, as long as both workflows belong to the same `WorkflowOwner` and happen to request the same Method/URL/Headers/Body combination. This could corrupt downstream on-chain or off-chain logic driven by the poisoned HTTP action data for the victim workflow. This does not meet the "cross-owner isolation is impossible" framing from the original question (owner isolation is intact), but it does violate the documented workflow-ID-level isolation guarantee for workflows under a shared owner.

### Likelihood Explanation
Requires the attacker to control (or compromise) a workflow node capable of issuing `OutboundHTTPRequest`s with `Store=true` under the **same `WorkflowOwner`** as the victim workflow, and to predict/match the exact Method/URL/Headers/Body of the victim's request. This is a realistic scenario for organizations running multiple workflows/versions under one owner where one workflow is less trusted or has broader input control (e.g., accepts external/dynamic parameters that get echoed into the HTTP request URL/body), enabling deliberate collision crafting.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`) in `OutboundHTTPRequest.Hash()` so that cache entries are strictly scoped per workflow, matching the documented isolation guarantee in `README.md`. Add/extend unit tests in `response_cache_test.go` to assert that identical Method/URL/Headers/Body/WorkflowOwner but differing `WorkflowID` produce different hashes and that `Set()` by one workflow cannot be retrieved by `Fetch()` for another.

### Proof of Concept
1. In `response_cache_test.go`, construct `req1` and `req2` via `createTestRequest("GET", "https://example.com")`, both with the same `WorkflowOwner` but `req1.WorkflowID = "workflow-A"`, `req2.WorkflowID = "workflow-B"`.
2. Call `cache.Set(req1, createTestResponse(200, "poisoned-by-A"))`.
3. Call `result := cache.Fetch(ctx, req2, fetchFnThatWouldReturnLegitimate, true)`.
4. Expected (secure) behavior: `result.Body` should be the legitimate fetched response, not `"poisoned-by-A"`, because req2 belongs to a different workflow.
5. Actual current behavior: since `req1.Hash() == req2.Hash()` (per the existing test at lines 139-149), `Fetch` for req2 hits the entry stored by req1 and returns `"poisoned-by-A"`, demonstrating cross-workflow leakage between workflows of the same owner.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L66-68)
```go
func (rc *responseCache) Fetch(ctx context.Context, req gateway.OutboundHTTPRequest, fetchFn func() gateway.OutboundHTTPResponse, storeOnFetch bool) gateway.OutboundHTTPResponse {
	cacheKey := req.Hash()
	cacheMaxAge := time.Duration(req.CacheSettings.MaxAgeMs) * time.Millisecond
```

**File:** core/services/gateway/handlers/capabilities/v2/response_cache.go (L111-119)
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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L151-161)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L66-72)
```markdown

- **Cacheable Responses**: 2xx (success) and 4xx (client error) status codes.
- **Cache TTL**: Configurable, default 10 minutes
- **Cache Key**: Generated from workflow ID and request hash
- **Cache Invalidation**: Time-based expiration with periodic cleanup
- **Cache Strategy**: All cacheable responses are cached; Non-zero `CacheSettings.MaxAgeMs` determines whether to return a cached value or make a fresh request
- **Workflow Isolation**: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage
```
