### Title
Cross-Workflow HTTP Response Cache Confusion Due to Undocumented/Mismatched Cache-Key Scope - (File: core/services/gateway/handlers/capabilities/v2/response_cache.go)

### Summary
The `README.md` for the HTTP Handlers V2 gateway component explicitly documents that outbound HTTP action responses are cached with a "Cache Key ... Generated from workflow ID and request hash" and that "Cache entries are scoped by workflow ID to prevent cross-workflow data leakage." [1](#0-0) . The actual implementation in `responseCache` does not scope by `WorkflowID` at all — the code comment states the key is "a hash of the request (method, URL, headers, body, workflowOwner)" [2](#0-1) , and this is confirmed by tests showing different `WorkflowID` values produce the *same* hash while different `WorkflowOwner` values produce different hashes [3](#0-2) . This is directly analogous to the external report's core problem: the criteria for a resource-isolation/exemption boundary ("cost-free wallet" selection in the BWG report; "cache scope" here) is not accurately documented or enforced as claimed, creating room for unintended cross-entity data sharing.

### Finding Description
`makeOutgoingRequest` in the gateway's HTTP handler reads `CacheSettings` and other fields straight from the `OutboundHTTPRequest` that is supplied by the workflow node making the outbound HTTP action call, then uses `responseCache.Fetch`/`Set` keyed purely by `req.Hash()` [4](#0-3) . `Hash()` (defined in the `gateway_common` package, not indexed in this scan) intentionally omits `WorkflowID` and only folds in `WorkflowOwner`, per the code comment and tests [2](#0-1) [5](#0-4) .

This means:
- Any two workflows owned by the same `WorkflowOwner` address that issue an HTTP action with the same method/URL/headers/body will share the exact same cache entry, regardless of `WorkflowID`.
- Since documentation (`README.md`) asserts workflow-ID-level isolation ("Workflow Isolation: Cache entries are scoped by workflow ID to prevent cross-workflow data leakage"), operators/integrators reasonably relying on that claim would incorrectly assume per-workflow cache isolation.
- A single compromised or malicious workflow belonging to a given owner could pre-warm the cache with a crafted 2xx/4xx response for a given URL/method/body combination (cache `Set` accepts any request as long as `CacheSettings.Store` is true and status is 2xx/4xx) [6](#0-5) , and a sibling, unrelated workflow under the same owner that later performs the "same" outbound HTTP action would silently receive that attacker-influenced cached response instead of making its own live HTTP request — a cross-workflow response-confusion condition.

This mirrors the external report's underlying issue exactly: an isolation/exemption boundary (cost-free wallet selection vs. cache workflow-isolation) that is not clearly/accurately specified, and whose actual criteria diverge from what is documented, opening room for abuse by an unprivileged actor who controls one workflow to affect another workflow's results.

### Impact Explanation
If exploited, a workflow owner (an unprivileged, non-operator actor who merely owns/operates workflows on the CRE platform) that controls two workflows can use one workflow to seed the shared cache with a forged HTTP action response, and have another of their own workflows (or the same one under a different `WorkflowID` context, if such context is otherwise assumed to be isolated) consume that same cached, potentially malicious response, since `WorkflowID` provides zero cache isolation contrary to the documented guarantee. Any downstream logic (e.g., alerting/oracle logic) that trusts documented per-workflow cache isolation could act on stale or attacker-influenced data from a different, unrelated workflow context. This is a documentation/implementation mismatch producing a real behavioral gap (cross-workflow response confusion) rather than a full cross-user authentication bypass, since the isolation boundary that actually holds is `WorkflowOwner`, not `WorkflowID`.

### Likelihood Explanation
Likelihood is moderate: it requires an actor to control (or have deployed) multiple workflows under the same `WorkflowOwner` and to know/guess the exact request shape (method, URL, headers, body) used by another of their own workflows to trigger cache collision — this is achievable without any additional privilege beyond normal workflow-deployment rights already granted to that owner. It does not require breaking authentication, JWT signing, or the allowlist; it only requires exploiting the gap between documented and actual cache-key semantics.

### Recommendation
Align the implementation with the documented guarantee or correct the documentation to match the code:
- If per-workflow isolation is the intended security property (as `README.md` states), include `WorkflowID` (and ideally `WorkflowExecutionID` where relevant) in `OutboundHTTPRequest.Hash()` in addition to `WorkflowOwner`.
- If per-owner scoping is the intentional design (e.g., to allow response sharing/caching efficiency across a single owner's workflows), update `README.md` and code comments to explicitly state that isolation is enforced at the `WorkflowOwner` level, not the `WorkflowID` level, and document the security implications for owners running multiple, mutually-distrusting workflows under one account.
- Add an explicit test asserting the documented behavior (or the corrected documented behavior) to prevent this drift from recurring.

### Proof of Concept
1. Deploy Workflow A and Workflow B, both owned by the same `WorkflowOwner` address.
2. From Workflow A, issue an `OutboundHTTPRequest` with `Method=GET`, `URL=https://victim-api.example.com/data`, no custom headers/body, and `CacheSettings{Store: true, MaxAgeMs: 600000}`. Have the gateway/mock endpoint return a 2xx forged payload.
3. Confirm via `responseCache.cache` (or via `TestRequestHash`/`TestFetch`-style behavior [7](#0-6) ) that the cache key is identical for a same-shaped request issued from Workflow B (different `WorkflowID`, same `WorkflowOwner`).
4. From Workflow B, issue the identical `OutboundHTTPRequest` shape with `CacheSettings{MaxAgeMs: 600000}`; observe it receives Workflow A's cached (forged) response via `Fetch` without making a new outbound call, contradicting the documented "Workflow Isolation" guarantee.

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
