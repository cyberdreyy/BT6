### Title
Unauthenticated `MethodPublicKeyGet` cache-miss fan-out lacks a rate limiter, enabling DON-side amplification / resource exhaustion - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` deliberately skips `h.requestProcessor.ProcessRequest` for `vaulttypes.MethodPublicKeyGet` because "Public key requests don't require authorization" [1](#0-0) . When `h.cachedPublicKeyGetResponse` is `nil`, every such request is turned into a new `activeRequest` and forwarded via `fanOutToVaultNodes`, which sends the request to **every** node in `h.donConfig.Members` [2](#0-1) . No identity- or IP-based rate limiter guards this unauthenticated path; `h.nodeRateLimiter` only throttles inbound *node* responses in `HandleNodeMessage`, not user requests [3](#0-2) .

### Finding Description
The only two protections on the request-intake side are (1) a JSON-RPC ID length cap of 200 characters [4](#0-3)  and (2) a uniqueness check per `req.ID` in `newActiveRequest`, which just rejects a *duplicate* ID rather than throttling distinct IDs [5](#0-4) . Neither limits the *rate* of distinct requests. The gateway's HTTP-layer protections (`MaxRequestBytesLimiter`) only bound payload size, not request frequency, and `ProcessRequest` in `gateway.go` performs no per-caller rate limiting before dispatching to the handler [6](#0-5) .

Consequently, while `h.cachedPublicKeyGetResponse` is unset (e.g., during startup before the first successful `fetchVaultPublicKey` periodic refresh, or any period the DON fails to answer that refresh), an unauthenticated attacker sending N concurrent `MethodPublicKeyGet` requests with unique `req.ID`s causes N × `len(donConfig.Members)` `don.SendToNode` calls, and N entries accumulate in `h.activeRequests` until `removeExpiredRequests` clears them after `requestTimeout` (default 30s, checked every 5s) [7](#0-6) , [8](#0-7) .

Mitigating factor: once `fetchVaultPublicKey` succeeds (attempted every minute via a background ticker, and also proactively on the first user cache-miss) [9](#0-8) , `h.cachedPublicKeyGetResponse` is populated and never expires (no TTL check is applied in `getCachedPublicKey`, despite `defaultPublicKeyGetCacheDurationSeconds` being declared but unused) [10](#0-9) . All subsequent requests take the synchronous cached path (`handlePublicKeyGetSynchronously`), which does not touch the DON at all [11](#0-10) . So the exploitable window is effectively limited to gateway startup / cold-cache periods, not a persistent steady-state condition.

### Impact Explanation
During the cache-cold window, an unauthenticated attacker can cause the gateway to fan out an unbounded number of node requests (bounded only by attacker throughput and DON member count) with no per-caller quota, and can grow `h.activeRequests` in memory until the timeout sweep runs. This matches a resource-exhaustion / availability-degradation impact class rather than authentication bypass or data disclosure — no secrets, keys, or cross-user data are exposed since the response returned is only the public key.

### Likelihood Explanation
Fully unauthenticated and trivially reproducible (no credentials, no signature needed), but the attack window is narrow and self-limiting in practice: the handler auto-fetches and permanently caches the public key on startup and every minute thereafter, so sustained abuse requires the DON to be persistently failing to answer `MethodPublicKeyGet` (an unusual precondition) or repeated gateway restarts. This meaningfully reduces exploitability compared to a persistent unauthenticated fan-out primitive.

### Recommendation
Add an unauthenticated-request rate limiter (e.g., a global `limits.RateLimiter`/`ratelimit.RateLimiter` keyed independently of `nodeRateLimiter`) that gates the cache-miss branch of `MethodPublicKeyGet` before calling `fanOutToVaultNodes`, and/or cap the number of concurrent in-flight `MethodPublicKeyGet` fan-outs to a single in-flight request (coalescing duplicate cache-miss callers onto one outstanding DON round trip) rather than one fan-out per request ID.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` via `newHandlerWithAuthorizer` with a mocked `don` (`gwhandlers.DON`) that counts `SendToNode` invocations, and ensure `h.cachedPublicKeyGetResponse == nil` (cache cold).
2. Fire N (e.g., 500) goroutines each calling `HandleJSONRPCUserMessage` with `Method: vaulttypes.MethodPublicKeyGet` and a unique `req.ID`, no `Auth`.
3. Assert `don.SendToNode` was called `N * len(donConfig.Members)` times and `len(h.activeRequests) == N` before the cleanup ticker fires, with no rejection/limiting error returned to any caller.
4. Assert (as the fix criterion) that after adding a global unauthenticated-request limiter, some subset of these calls return `api.LimitExceededError` instead of being forwarded to `don.SendToNode`.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L43-46)
```go
const (
	defaultCleanUpPeriod                    = 5 * time.Second
	defaultPublicKeyGetCacheDurationSeconds = 300
)
```

**File:** core/services/gateway/handlers/vault/handler.go (L287-304)
```go
		go func() {
			ctx, cancel := h.stopCh.NewCtx()
			defer cancel()
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
				case <-h.stopCh:
					return
				}
			}
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

**File:** core/services/gateway/handlers/vault/handler.go (L404-410)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L489-496)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L670-680)
```go
func (h *handler) getCachedPublicKey() ([]byte, *tdh2easy.PublicKey) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	if h.cachedPublicKeyGetResponse == nil {
		return nil, nil
	}
	copied := make([]byte, len(h.cachedPublicKeyGetResponse))
	copy(copied, h.cachedPublicKeyGetResponse)
	cachedPublicKeyCopy := *h.cachedPublicKeyObject
	return copied, &cachedPublicKeyCopy
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L682-741)
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

func (h *handler) handlePublicKeyGetSynchronously(ctx context.Context, req jsonrpc.Request[json.RawMessage], publicKeyResponseBytes []byte, callback gwhandlers.Callback) error {
	resp := jsonrpc.Response[json.RawMessage]{
		Version: jsonrpc.JsonRpcVersion,
		ID:      req.ID,
		Method:  req.Method,
		Result:  (*json.RawMessage)(&publicKeyResponseBytes),
	}
	rawResponse, err := jsonrpc.EncodeResponse(&resp)
	if err != nil {
		h.metrics.requestInternalError.Add(ctx, 1, metric.WithAttributes(
			attribute.String("don_id", h.donConfig.DonId),
			attribute.String("error", api.NodeReponseEncodingError.String()),
		))
		h.lggr.Errorw("failed to encode response", "error", err)
		return errors.New("failed to marshal response: " + err.Error())
	}
	successResp := gwhandlers.UserCallbackPayload{
		RawResponse: rawResponse,
		ErrorCode:   api.NoError,
	}
	h.metrics.requestSuccess.Add(ctx, 1, metric.WithAttributes(
		attribute.String("don_id", h.donConfig.DonId),
	))
	return callback.SendResponse(successResp)
}

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
```

**File:** core/services/gateway/gateway.go (L218-276)
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
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}

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
