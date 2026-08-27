### Title
Unauthenticated, unrate-limited amplification of DON fan-out via repeated `MethodPublicKeyGet` requests before public-key cache is warm - ([File: core/services/gateway/handlers/vault/handler.go])

### Finding Description
In `(*handler).HandleJSONRPCUserMessage`, `vaulttypes.MethodPublicKeyGet` is explicitly special-cased to skip `h.requestProcessor.ProcessRequest` authorization ("Public key requests don't require authorization") [1](#0-0) . When `h.getCachedPublicKey()` returns `nil` (cold start, right after cache invalidation, or before the periodic `fetchVaultPublicKey` ticker first populates it), each request calls `h.newActiveRequest(req, callback)` and then `h.handlePublicKeyGet`, which calls `h.fanOutToVaultNodes` to send the request to every DON member [2](#0-1) [3](#0-2) .

`newActiveRequest` only rejects a request if its exact `req.ID` string already exists in the `activeRequests` map [4](#0-3) ; it does not limit the total number of concurrent entries. Entries are only removed on completion or after `h.requestTimeout` (default 30s) via `removeExpiredRequests` [5](#0-4) .

Tracing the ingress path: `gateway.ProcessRequest` (HTTP layer) only enforces a request-ID length cap (200 chars) and a `MaxRequestBytesLimiter` on body size [6](#0-5) , then dispatches directly to `multiHandler.HandleJSONRPCUserMessage` → vault `handler.HandleJSONRPCUserMessage` with no user-level or per-IP rate limiting [7](#0-6) . The only `RateLimiter` present in the vault handler, `h.nodeRateLimiter`, gates `HandleNodeMessage` (DON node responses coming back into the gateway), not the user-request ingress path used for `MethodPublicKeyGet` [8](#0-7) . Thus, an unauthenticated caller sending many `MethodPublicKeyGet` requests with unique `req.ID`s during any window where the cache is not yet warm can create an unbounded number of `activeRequest` entries and trigger a full DON fan-out for each one, with zero credentials required.

### Impact Explanation
This is a resource-exhaustion/amplification vector: a single unauthenticated HTTP client can multiply its request volume by the number of DON members via `fanOutToVaultNodes`, and can grow the gateway's in-memory `activeRequests` map unboundedly for up to `requestTimeout` (30s) per burst, repeatable indefinitely by sending fresh unique IDs. This does not itself compromise keys/secrets or bypass authorization for management operations (secrets create/update/delete/list remain gated by `h.requestProcessor.ProcessRequest`), but it can degrade or exhaust gateway/DON resources — a denial-of-service class impact, not privilege escalation or key disclosure.

### Likelihood Explanation
Preconditions are minimal: only knowledge of the gateway URL is required, and the attack works during any window in which `cachedPublicKeyObject` is nil (service startup, before the first successful `fetchVaultPublicKey` completes, or if fetch failures/slow DON responses keep the cache cold). No signature, JWT, or allow-list credential is checked for this method. The comment in the code ("we cache this value quite aggressively so don't need to worry about DoS") is only true once the cache is warm; it does not hold for the cold-cache window, and an attacker can potentially keep that window open by also stressing the DON (out of scope) or simply catching restarts/cache resets.

### Recommendation
Add a lightweight rate limiter (global and/or per-IP) gating the cache-miss branch of `MethodPublicKeyGet` in `HandleJSONRPCUserMessage`, independent of `req.ID` uniqueness — e.g., coalesce concurrent cache-miss requests into a single in-flight fetch (singleflight pattern) so that only one `newActiveRequest`/`fanOutToVaultNodes` call is issued per cache-miss period regardless of how many distinct unauthenticated requests arrive.

### Proof of Concept
Go handler-level test plan:
1. Construct a `handler` with a fake `clock`, a `don` mock (`gwhandlers.DON`) that counts `SendToNode` invocations, and a `donConfig` with N members.
2. Ensure `cachedPublicKeyGetResponse`/`cachedPublicKeyObject` are nil (cold cache).
3. In a loop, call `handler.HandleJSONRPCUserMessage` with `vaulttypes.MethodPublicKeyGet` and a fresh, unique `req.ID` for, e.g., 1000 iterations, without ever satisfying the cache (don't let the mocked DON respond).
4. Assert: `len(h.activeRequests)` grows linearly with the number of requests (unbounded) and `don.SendToNode` call count equals `1000 * N` (full fan-out per request), demonstrating no rate limiter or dedup mechanism bounds `newActiveRequest`/DON fan-out calls for this unauthenticated method.
5. Expected (failing) assertion for a fixed version: `don.SendToNode` call count should be bounded (e.g., capped at `N` due to in-flight de-duplication) regardless of the number of unique-ID requests sent during the cache-miss window.

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

**File:** core/services/gateway/handlers/vault/handler.go (L413-426)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L489-497)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}

```

**File:** core/services/gateway/handlers/vault/handler.go (L682-698)
```go
func (h *handler) handlePublicKeyGet(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
	if cachedPublicKey != nil {
		l.Debugw("returning cached public key response")
		return h.sendSuccessResponse(ctx, l, ar, &jsonrpc.Response[json.RawMessage]{
			Version: jsonrpc.JsonRpcVersion,
			ID:      ar.req.ID,
			Method:  ar.req.Method,
			Result:  (*json.RawMessage)(&publicKeyResponseBytes),
		})
	}

	l.Debugw("cache stale: forwarding request to nodes", "now", h.clock.Now())
	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
}
```

**File:** core/services/gateway/gateway.go (L218-231)
```go
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
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
