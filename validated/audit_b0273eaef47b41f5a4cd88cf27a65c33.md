### Title
Vault gateway handler's `activeRequests` map grows unbounded per client-supplied request ID with no admission cap - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
The `vault` gateway handler stores every in-flight `eth_...`/vault JSON-RPC user request in an in-memory `map[string]*activeRequest` keyed by the caller-supplied `req.ID`, with cleanup only occurring on completion or via a periodic timeout sweep. Unlike the sibling `RequestCache` implementation used by other handlers, `handler.activeRequests` has no configured maximum size enforced at insertion time, mirroring the Besu `FilterRepository` bug class: unbounded growth of a request-keyed map from client input with only time-based (not count-based) eviction.

### Finding Description
`handler.newActiveRequest` inserts a new entry into `h.activeRequests` for every incoming user request, keyed only by the client-controlled `req.ID` (bounded merely to 200 characters), and only rejects a request if that specific ID is already present — there is no check against a maximum map size: [1](#0-0) 

Compare this to `core/services/gateway/handlers/common/requestcache.go`, which is the pattern used elsewhere in the gateway and explicitly enforces `maxCacheSize` at request-creation time: [2](#0-1) 

The vault handler has no equivalent check. Its only reclamation mechanism is `removeExpiredRequests`, which runs on a timer/sweep and removes entries only after `requestTimeout` (default 30s) elapses: [3](#0-2) 

`HandleJSONRPCUserMessage` is the entry point invoked for user-submitted requests. For `vaulttypes.MethodPublicKeyGet`, the code explicitly notes that "Public key requests don't require authorization" and creates a new `activeRequest` immediately when the cached public key is unavailable — before any signature/authorization check: [4](#0-3) 

For other vault methods, `ProcessRequest`/authorization is checked first, but that check validates a signature over caller-controlled data — it does not bound the number of distinct requests an authorized (or, for `MethodPublicKeyGet`, entirely unauthenticated) actor can create concurrently. Each `activeRequest` also carries a `responses` map that accumulates entries as it fans out to DON nodes, adding further per-request memory.

This request flow is reached through the general gateway dispatch path (`multiHandler.HandleJSONRPCUserMessage` → per-method handler), which is the internet/DON-facing entry point for user JSON-RPC calls: [5](#0-4) 

### Impact Explanation
An unprivileged/unauthenticated caller (at minimum via the no-auth `MethodPublicKeyGet` path when the public key is not yet cached, and more generally any caller who can produce syntactically valid requests with unique IDs) can create an unbounded number of entries in `handler.activeRequests` by submitting requests with distinct `req.ID` values faster than the `requestTimeout` sweep can reclaim them. Each entry holds a `Callback`, request payload, and a growing per-node `responses` map, so sustained request volume can drive unbounded heap growth on the gateway node, leading to memory exhaustion / denial of service for the DON gateway process — directly analogous to the Besu `FilterRepository` unbounded-map finding.

### Likelihood Explanation
Likelihood is moderate: the attacker only needs network access to the gateway's JSON-RPC endpoint and the ability to generate unique request IDs (trivial, e.g., random strings under 200 chars) faster than the fixed 30-second default expiry reclaims them. No authentication is required at all for the `MethodPublicKeyGet` cache-miss path. For other methods, a valid signature is required, but the authorization check does not throttle the *number* of distinct concurrently-tracked requests, only whether an individual request is authorized — so any party capable of producing valid signed requests (which may be more than one privileged principal, depending on the enclave's authorization policy) can equally exhaust the map.

### Recommendation
Add a configurable maximum on `len(h.activeRequests)` enforced inside `newActiveRequest` (mirroring `requestCache.NewRequest`'s `maxCacheSize` check), returning an explicit "too many active requests" error once the cap is reached, and consider tightening/reducing the default `requestTimeout` or adding per-source-address quotas for the unauthenticated `MethodPublicKeyGet` path specifically, since it bypasses authorization entirely.

### Proof of Concept
1. Repeatedly call the vault gateway's `MethodPublicKeyGet` JSON-RPC method (or any vault method, if valid signatures can be produced) with a fresh unique `req.ID` on each call, faster than the 30-second default `RequestTimeoutSec` window.
2. Observe `handler.activeRequests` (and the per-request `responses` maps within each `activeRequest`) growing without bound, since `newActiveRequest` only checks for ID collision, not total map size, before inserting.
3. Continued flooding drives up gateway memory usage, eventually causing degraded performance or OOM on the gateway node — the same impact class described in the Besu advisory for `eth_newFilter`.

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

**File:** core/services/gateway/handlers/vault/handler.go (L403-429)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	h.lggr.Debugw("handling vault request", "method", req.Method, "requestID", req.ID, "request", req)
	if req.Method == vaulttypes.MethodPublicKeyGet {
		// Public key requests don't require authorization,
		// Let's process this request right away.
		// Note we cache this value quite aggressively so don't need to worry about DoS.
		publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
		if cachedPublicKey == nil {
			// Not found in cache. Fetch from nodes.
			ar, err := h.newActiveRequest(req, callback)
			if err != nil {
				h.lggr.Errorw("failed to create new activeRequest", "error", err)
				return err
			}
			return h.handlePublicKeyGet(ctx, ar)
		}
		h.lggr.Debugw("returning cached public key response")
		return h.handlePublicKeyGetSynchronously(ctx, req, publicKeyResponseBytes, callback)
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

**File:** core/services/gateway/multihandler.go (L62-69)
```go
func (m *multiHandler) HandleJSONRPCUserMessage(ctx context.Context, jsonRequest jsonrpc.Request[json.RawMessage], callback handlers.Callback) error {
	h, err := m.getHandler(jsonRequest.Method)
	if err != nil {
		return fmt.Errorf("failed to get handler for method %s: %w", jsonRequest.Method, err)
	}

	return h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
}
```
