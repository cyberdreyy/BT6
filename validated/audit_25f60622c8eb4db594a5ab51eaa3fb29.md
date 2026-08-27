### Title
Unbounded growth of `activeRequests` map from unauthenticated/unbounded user JSON-RPC requests causes gateway resource exhaustion - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
The Gateway's `vault` and `confidentialrelay` handlers store every incoming user request in an `activeRequests` map keyed by the caller-supplied `req.ID`, with no cap on the number of concurrent entries. Entries are only removed either when a full quorum of node responses arrives or by a periodic time-based reaper (`removeExpiredRequests`). This mirrors the reported bug class ("array is pushed but not popped, and is iterated over"): a data structure grows on every unprivileged request and is later iterated in full by cleanup logic, with no bound on the number of entries that can accumulate between reaper cycles.

### Finding Description
`newActiveRequest` inserts a new entry into `h.activeRequests` for every incoming request, checking only that the ID isn't already in use — never checking a maximum size: [1](#0-0) 

Compare this to `RequestCache`, the pattern the codebase itself uses elsewhere, which explicitly enforces `maxCacheSize` before accepting a new request: [2](#0-1) 

The only cleanup for `activeRequests` is a periodic, time-based reaper that iterates the *entire* map under a lock and removes only entries older than `requestTimeout`: [3](#0-2) 

The identical pattern (unbounded map insert + periodic full-scan expiry, no max-size guard) exists in the `confidentialrelay` handler as well: [4](#0-3) 

Because `req.ID` is attacker-controlled and each unique ID creates a new map entry, a client sending many JSON-RPC requests with distinct IDs in a short burst can accumulate an unbounded number of `activeRequest` objects (each holding a `responses` map, callback, and metadata) before the reaper's next tick reclaims them. The reaper itself scales linearly with map size and holds `h.mu` (RLock, but `sendResponse`/`newActiveRequest` take the write lock) while building the list of expired entries, so a large map increases lock contention and reaping cost for the entire gateway instance handling that DON.

### Impact Explanation
Uncontrolled memory growth and increased lock-hold time in the request-handling hot path can degrade or exhaust gateway node resources (memory, GC pressure, lock contention), delaying or dropping legitimate vault/confidential-relay requests for all users routed through the same DON gateway — a denial-of-service on a shared, internet-facing component. This does not by itself leak secrets or bypass authorization, but it can starve fair users' quorum-based flows (vault secret retrieval, TEE-relayed capability execution), which is a service-affecting impact on an unprivileged-reachable message path.

### Likelihood Explanation
Medium. Exploitation requires only the ability to send many distinct JSON-RPC requests to the gateway's public/user-facing message handler within a window shorter than `requestTimeout`/reaper period — no privileged role or node compromise is needed. However, other layers (rate limiters such as `nodeRateLimiter`/`userRateLimiter`, request-ID authorization keys for some paths) may partially mitigate volumetric abuse depending on configuration, and I could not fully verify from available context whether a size cap or additional gating is applied earlier in `HandleJSONRPCUserMessage` before `newActiveRequest` is invoked.

### Recommendation
- Add an explicit maximum-size check on `h.activeRequests` in `newActiveRequest` (mirroring `RequestCache.maxCacheSize`), rejecting new requests with a clear rate-limit/capacity error once the bound is reached.
- Consider shortening the reap interval or triggering eviction sooner under memory pressure, and avoid holding the request map lock for the duration of large iteration passes.
- Apply the same fix consistently to both `core/services/gateway/handlers/vault/handler.go` and `core/services/gateway/handlers/confidentialrelay/handler.go`.

### Proof of Concept
1. As an unprivileged gateway client, submit many JSON-RPC requests to the vault or confidentialrelay handler's user-message endpoint, each with a unique `req.ID`, faster than `requestTimeout`/the reaper interval.
2. Each request creates a new entry in `h.activeRequests` via `newActiveRequest` (no size check), growing the map without bound. [1](#0-0) 
3. Observe increased memory usage and reaper (`removeExpiredRequests`) latency as it must scan the enlarged map, degrading gateway responsiveness for all users of that DON.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L369-393)
```go
// removeExpiredRequests removes expired requests from the pending requests map
func (h *handler) removeExpiredRequests(ctx context.Context) {
	h.mu.RLock()
	var expiredRequests []*activeRequest
	now := h.clock.Now()
	for _, userRequest := range h.activeRequests {
		if now.Sub(userRequest.createdAt) > h.requestTimeout {
			expiredRequests = append(expiredRequests, userRequest)
		}
	}
	h.mu.RUnlock()

	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		var nodeResponses strings.Builder
		for nodeKey, nodeResponse := range responses {
			_, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
		}
		nodeResponsesStr := nodeResponses.String()
		err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
		if err != nil {
			h.lggr.Errorw("error sending response to user", "requestID", er.req.ID, "error", err)
		}
	}
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-66)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```
