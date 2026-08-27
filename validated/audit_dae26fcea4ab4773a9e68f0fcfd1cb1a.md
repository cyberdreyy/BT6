### Title
Unauthenticated flooding of `vault.publicKey.get` causes unbounded `activeRequests` growth and DON fan-out - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` intentionally skips authorization for `MethodPublicKeyGet`, and when the public key cache is empty/stale it calls `h.newActiveRequest` and `h.fanOutToVaultNodes` for every distinct `req.ID` supplied by the caller [1](#0-0) . No per-caller quota exists anywhere upstream of this branch, so an unauthenticated flood of requests with unique IDs will grow `h.activeRequests` unbounded and multiply outbound requests to all DON members.

### Finding Description
In `HandleJSONRPCUserMessage`, the `MethodPublicKeyGet` branch is reached before the JWT/allow-list authorization step (`h.requestProcessor.ProcessRequest`) that gates all other vault methods [2](#0-1) . When `h.getCachedPublicKey()` returns `nil` (cold/stale cache), the handler calls `h.newActiveRequest(req, callback)` which inserts an entry into the shared `h.activeRequests` map keyed by the caller-supplied `req.ID`, and then calls `h.handlePublicKeyGet`, which in turn calls `h.fanOutToVaultNodes` to send the request to every member of `h.donConfig.Members` [3](#0-2) [4](#0-3) [5](#0-4) .

The only guard on `req.ID` is a length/emptiness check (max 200 chars) [6](#0-5) , so an attacker can generate arbitrarily many unique IDs. `newActiveRequest` only rejects a duplicate ID that is *already* pending; it does not check any total map size or per-caller count [3](#0-2) . Entries are only removed either when a response completes the round trip (`sendResponse` deletes the map entry) or by the periodic `removeExpiredRequests` background goroutine, which runs every `defaultCleanUpPeriod` (5s) and only evicts entries older than `h.requestTimeout` (default 30s, configurable) [7](#0-6) [8](#0-7) . This means an attacker can accumulate roughly `requestTimeout / avg-request-interval` outstanding entries at any time, and each entry also triggers a full node fan-out (`SendToNode` to every DON member) before being cleaned up.

At the HTTP transport layer (`core/services/gateway/network/httpserver.go`), the only protective control is a request body size limiter (`MaxRequestBytesLimiter`) — there is no per-IP/per-caller request rate limiter before `ProcessRequest` is invoked [9](#0-8) . The gateway's `ProcessRequest` routes directly to `HandleJSONRPCUserMessage` with no additional throttling [10](#0-9) . `h.nodeRateLimiter` in the vault handler is only applied to node *responses* in `HandleNodeMessage`, not to inbound user requests [11](#0-10) , so it does nothing to bound this path.

While the cache is normally refreshed every minute by a background ticker (`fetchVaultPublicKey`) and is described as being cached "aggressively" [12](#0-11) [13](#0-12) , that comment does not eliminate the window between handler start and first successful cache population, nor scenarios where the DON is slow/unavailable and the cache never populates (e.g., all nodes failing to respond, or persistent node connectivity issues), during which every `publicKey.get` call falls through to the uncached/no-limit path indefinitely.

### Impact Explanation
Sustained flooding causes `h.activeRequests` to grow with attacker-controlled memory allocations (each `activeRequest` holds a mutex, a map, and a copy of the request) and multiplies outbound calls to `h.don.SendToNode` for every DON member per flood request. This degrades gateway resource availability and vault-node request bandwidth shared with legitimate authorized users of the same handler instance (i.e., secrets create/update/delete/list requests funnel through the same `activeRequests` map, mutex, and DON connection), matching a resource-exhaustion / denial-of-service impact class rather than an authentication/authorization bypass.

### Likelihood Explanation
No credentials or preconditions are required — this is the explicitly-unauthenticated branch of the handler by design. The only precondition for the attack window to matter is that `h.cachedPublicKeyGetResponse` is nil/stale (true at startup, or any time the DON is slow/unreachable to satisfy `fetchVaultPublicKey`). The attack is trivially repeatable by any HTTP client capable of reaching the gateway's user-facing port with distinct `req.ID` values (up to 200 chars each), requiring no signature or JWT.

### Recommendation
Add a per-caller/per-IP (or global) rate limiter and/or a hard cap on the number of concurrently pending `activeRequests` entries that applies specifically to the unauthenticated `MethodPublicKeyGet` cache-miss path (e.g., reuse `limits.RateLimiter`/`GateLimiter` primitives already used elsewhere in this file, such as `writeMethodsEnabled`), so that flooding with unique request IDs cannot grow `activeRequests` or trigger unbounded `fanOutToVaultNodes` calls beyond a bounded, cheap, single-flight cache-refill request. Consider collapsing concurrent cache-miss `publicKey.get` requests into a single in-flight upstream fetch (single-flight pattern) rather than fanning out to nodes once per caller request.

### Proof of Concept
Go handler-level integration test plan:
1. Construct a `handler` via `newHandlerWithAuthorizer` with a mock `don.SendToNode` that counts invocations, and a `clock` fake that never fires the 1-minute public-key-refresh ticker (simulating a cold cache).
2. Do not seed `h.cachedPublicKeyGetResponse`.
3. In a loop, call `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request{Method: vaulttypes.MethodPublicKeyGet, ID: uuid.New().String()}, noopCallback)` N times (e.g., 10,000) without waiting for responses, with no `Auth` field set.
4. Assert: (a) no error is returned for any of the N calls (confirming no authorization/quota check blocks them), (b) `len(h.activeRequests) == N` before cleanup runs, showing unbounded growth, and (c) `don.SendToNode` was called `N * len(donConfig.Members)` times, confirming unbounded fan-out.
5. Compare against `MethodSecretsList` (an authorized method) to show that only `MethodPublicKeyGet` bypasses any admission control before mutating `activeRequests` and fanning out.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L43-46)
```go
const (
	defaultCleanUpPeriod                    = 5 * time.Second
	defaultPublicKeyGetCacheDurationSeconds = 300
)
```

**File:** core/services/gateway/handlers/vault/handler.go (L290-300)
```go
			ticker := h.clock.NewTicker(defaultCleanUpPeriod)
			tickerVaultPublicKeyRefresh := h.clock.NewTicker(1 * time.Minute)
			defer ticker.Stop()
			defer tickerVaultPublicKeyRefresh.Stop()
			for {
				select {
				case <-ticker.Chan():
					h.removeExpiredRequests(ctx)
				case <-tickerVaultPublicKeyRefresh.Chan():
					// periodically, fetch vault public key, so we can cache it
					h.fetchVaultPublicKey(ctx)
```

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

**File:** core/services/gateway/handlers/vault/handler.go (L403-410)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L413-429)
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

**File:** core/services/gateway/network/httpserver.go (L196-219)
```go
	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		msg := "Failed to get request size limit"
		s.lggr.Errorw(msg, "err", err)
		http.Error(w, msg, http.StatusInternalServerError)
		return
	}
	source := http.MaxBytesReader(nil, r.Body, int64(maxRequestBytes))
	rawMessage, err := io.ReadAll(source)
	if err != nil {
		s.lggr.Error("error reading request", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}

	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
```

**File:** core/services/gateway/gateway.go (L264-277)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

```
