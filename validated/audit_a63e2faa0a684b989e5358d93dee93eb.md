### Title
HTTP Action response cache key omits `WorkflowID`, allowing cross-workflow response confusion for the same workflow owner - ([File: core/services/gateway/handlers/capabilities/v2/response_cache.go])

### Summary
The `responseCache` used by the gateway's HTTP capability handler keys cached HTTP responses by `OutboundHTTPRequest.Hash()`. That hash intentionally includes `WorkflowOwner` but explicitly excludes `WorkflowID`, contrary to the component's own documentation, which states caching provides "Workflow Isolation... scoped by workflow ID to prevent cross-workflow data leakage." This is structurally the same bug class as the `MerkleDistributor.verifyClaim()` report: a piece of context (`token` in the report, `WorkflowID` here) that should scope/bind a lookup/verification is left out of the data used to compute the key, letting a request from one context (workflow) be satisfied with data intended for another.

### Finding Description
`gateway.OutboundHTTPRequest.Hash()` is used as the cache key in `Fetch`/`Set`/`isExpiredOrNotCached` of `responseCache`: [1](#0-0) [2](#0-1) 

The test suite explicitly documents and asserts this behavior: identical requests differing only by `WorkflowID` produce the *same* hash, while differing `WorkflowOwner` values produce different hashes: [3](#0-2) 

However, the package's own README describes the intended security property as workflow-ID-scoped isolation: [4](#0-3) 

The cache is populated and read from `makeOutgoingRequest`, which is invoked directly from data received over the node connection for HTTP Action responses (`gateway_common.OutboundHTTPRequest` is unmarshalled from the node message and reused as the cache key input): [5](#0-4) 

`WorkflowID` is a genuine field on the request struct (used elsewhere for isolation/validation), so its intentional omission from `Hash()` is a design decision, not an accidental encoding artifact: [6](#0-5) 

### Impact Explanation
Because the hash omits `WorkflowID`, two different workflows belonging to the same `WorkflowOwner` that issue HTTP Action requests with the same `Method`/`URL`/`Headers`/`Body` will collide on the same cache entry. If `CacheSettings.Store`/`MaxAgeMs` are used, one workflow can receive the cached HTTP response that was actually produced for a different, unrelated workflow (potentially with different authorization context, secrets injected in headers/body, or expected response semantics). This is a cross-workflow response confusion analogous to Alice receiving WETH instead of DAI in the original report — the recipient gets data that was fetched/intended for a different logical context, even though the top-level owner-based rate limit/allowlist checks pass.

### Likelihood Explanation
This requires no privileged access — any workflow node/owner that operates two or more workflows (or workflows sharing the same `WorkflowOwner` value, depending on how the field is populated/trusted) hitting an identical URL/method/body can trigger the collision purely through normal capability usage (`CacheSettings.Store=true` and `MaxAgeMs>0`), which are legitimate options exposed in `OutboundHTTPRequest`. The bug is deterministic and reproducible via the existing test (`TestRequestHash` at lines 139-149), not merely theoretical.

### Recommendation
Include `WorkflowID` (in addition to `WorkflowOwner`, method, URL, headers, and body) in `OutboundHTTPRequest.Hash()` so that cache entries are strictly isolated per workflow, matching the documented "Workflow Isolation" guarantee. Update `response_cache_test.go` to assert those hashes now differ, and audit any other consumers relying on the current hash semantics for compatibility.

### Proof of Concept
1. Workflow A (owner `O`, workflow ID `wf-1`) issues an `OutboundHTTPRequest{Method:"GET", URL:"https://api.example.com/data", WorkflowOwner:"O", WorkflowID:"wf-1", CacheSettings:{Store:true, MaxAgeMs:600000}}`. The gateway fetches and caches the (possibly workflow-A-specific) response under `req.Hash()`.
2. Workflow B (same owner `O`, workflow ID `wf-2`) issues an identical `OutboundHTTPRequest` except `WorkflowID:"wf-2"`.
3. Per `TestRequestHash`'s own assertion (lines 139-149), `req1.Hash() == req2.Hash()` despite differing `WorkflowID`, so `responseCache.Fetch` in `http_handler.go` (lines 433-435) returns Workflow A's cached response to Workflow B without ever contacting the external endpoint — confirming cross-workflow response leakage/confusion.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/response_cache_test.go (L139-176)
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

**File:** core/services/gateway/handlers/capabilities/webapi.go (L5-15)
```go
type Request struct {
	URL       string            `json:"url"`                 // URL to query, only http and https protocols are supported.
	Method    string            `json:"method,omitempty"`    // HTTP verb, defaults to GET.
	Headers   map[string]string `json:"headers,omitempty"`   // HTTP headers, defaults to empty.
	Body      []byte            `json:"body,omitempty"`      // HTTP request body
	TimeoutMs uint32            `json:"timeoutMs,omitempty"` // Timeout in milliseconds

	// Maximum number of bytes to read from the response body.  If the gateway max response size is smaller than this value, the gateway max response size will be used.
	MaxResponseBytes uint32 `json:"maxBytes,omitempty"`
	WorkflowID       string
}
```
