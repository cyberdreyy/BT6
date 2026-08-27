Confirmed: there is no per-sender or global rate limiting anywhere in the HTTP ingress path (`httpserver.go` `handleRequest`, `gateway.go` `ProcessRequest`) before requests reach `handler.HandleJSONRPCUserMessage`. The only `ratelimit.RateLimiter` in the vault handler, `h.nodeRateLimiter`, is applied solely to node→gateway responses in `HandleNodeMessage`, not to inbound user requests. The `MethodPublicKeyGet` branch is explicitly documented as bypassing `h.requestProcessor.ProcessRequest` (the allowlist/JWT/quota authorization gate).

### Title
Unauthenticated `MethodPublicKeyGet` requests bypass authorization and rate limiting, allowing unbounded growth of `activeRequests` map - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` to skip `h.requestProcessor.ProcessRequest` entirely, and while the cache is cold it calls `h.newActiveRequest(req, callback)` keyed by the attacker-supplied `req.ID` before any authorization or per-sender throttling is applied. No component in the ingress path (`network/httpserver.go`'s `handleRequest`, `gateway.go`'s `ProcessRequest`) enforces per-sender or global request-rate limits on inbound user messages, so an unauthenticated client can flood unique-ID `MethodPublicKeyGet` requests to grow `h.activeRequests` and trigger repeated `fanOutToVaultNodes` calls.

### Finding Description
In `core/services/gateway/handlers/vault/handler.go`, `HandleJSONRPCUserMessage`: [1](#0-0) 
checks `req.Method == vaulttypes.MethodPublicKeyGet` and, when `h.getCachedPublicKey()` returns nil (cache cold, e.g. at gateway startup before the 1-minute `tickerVaultPublicKeyRefresh` first fires, or before the DON has ever answered), it calls `h.newActiveRequest(req, callback)`: [2](#0-1) 
which inserts an entry into `h.activeRequests` keyed by the caller-controlled `req.ID` — the only guard is a length check (`req.ID` ≤ 200 chars) done earlier in the same function: [3](#0-2) 
and duplicate-ID rejection, which an attacker trivially avoids by using unique IDs (e.g. UUIDs) per request.

Upstream, the gateway's HTTP ingress (`ProcessRequest` in `gateway.go`) and `handleRequest` in `network/httpserver.go` perform no authentication and no per-sender/global rate limiting before dispatching to `HandleJSONRPCUserMessage`: [4](#0-3) [5](#0-4) 
Only request-body size is bounded (`MaxRequestBytesLimiter`); there is no `ratelimit.RateLimiter`/quota check comparable to what protects `HandleNodeMessage` via `h.nodeRateLimiter`: [6](#0-5) 
That node-side limiter only throttles node responses, not incoming user requests, so it does not protect this path.

Each cache-miss `MethodPublicKeyGet` also triggers `fanOutToVaultNodes`, sending a message to every DON member node per attacker request: [7](#0-6) 
Entries are only cleaned up on quorum/timeout via `removeExpiredRequests`, bounded by `h.requestTimeout` (default 30s), so an attacker flooding unique-ID requests during the cache-cold window can grow the map to `requestTimeout / request-interval` size and cause repeated fan-out traffic to DON nodes, all without any authentication, allowlist check, or JWT/API-key requirement.

Regarding the "smuggle non-empty `req.Auth`/malicious `req.Params`" part of the question: `req.Auth` and `req.Params` are not used or forwarded meaningfully by this branch — `handlePublicKeyGet` builds/forwards `ar.req` (which is the same request struct) to nodes via `fanOutToVaultNodes`, and only `req.Method`/`req.ID` matter for routing/caching logic; there's no evidence that `req.Auth` or `req.Params` get parsed, executed, or that mismatched Params corrupt other users' cached data — `tryCachePublicKeyResponse` (called only for node-originated responses, not attacker-forged request fields) validates the public key format before caching. So the "malicious Params get logged/cached" component of the question is not substantiated by the code; only the debug log at line 412 logs the raw request, which is normal operational logging, not a distinct security bypass.

### Impact Explanation
This is a resource-exhaustion / quota-bypass issue on the Gateway component (not the node itself): an unauthenticated attacker can grow the in-memory `h.activeRequests` map and cause repeated fan-out network calls to all DON member nodes during any window where the public-key cache is cold, without needing any credential, allowlist entry, or JWT. This matches the "allowlist/quota bypass" impact class described in the question, scoped to the Gateway's vault handler — it does not achieve node key/secret disclosure, authentication bypass into privileged operations, or fund movement, since only the (soon-to-be-public) TDH2 public key request is affected.

### Likelihood Explanation
Exploitability is time-bounded and depends on the cache being cold: this occurs at gateway startup (before the first successful `fetchVaultPublicKey` succeeds) and any period the DON fails to answer quorum. Once the DON supplies a quorum answer, `tryCachePublicKeyResponse` populates `h.cachedPublicKeyObject` permanently for the life of the process, after which all further `MethodPublicKeyGet` requests are served synchronously via `handlePublicKeyGetSynchronously` without touching `activeRequests` at all: [8](#0-7) 
So the exploitable window is narrow (bounded by startup time until cache warms, typically well under the periodic 1-minute refresh interval) and self-healing; sustained abuse is not possible once cache is warm. Within that window, the attack is trivially repeatable (no auth needed) and only limited by HTTP throughput and `MaxRequestBytesLimiter`.

### Recommendation
Add a lightweight per-sender/global rate limiter (or reuse an existing gate limiter pattern like `writeMethodsEnabled`/`signedResponseRequestIDEnabled`) that gates the `MethodPublicKeyGet` cache-miss branch and/or `newActiveRequest`, independent of `h.requestProcessor.ProcessRequest`. Alternatively, bound `h.activeRequests` size explicitly (reject new entries beyond a configured cap) and/or coalesce concurrent cache-miss `MethodPublicKeyGet` requests into a single in-flight fan-out rather than creating one `activeRequest`/fan-out per incoming request.

### Proof of Concept
Go handler-level test plan:
1. Construct a `handler` via `newHandlerWithAuthorizer` with a `donConfig` of N mock nodes and no cached public key set.
2. Mock `don.SendToNode` to return `nil` for every call, and never deliver node responses (simulate cache staying cold).
3. From N concurrent goroutines, call `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request{Method: vaulttypes.MethodPublicKeyGet, ID: uuid.New().String()}, callback)` with unique IDs and no `Auth`.
4. Assert all calls succeed without error (no authorization or rate-limit rejection).
5. Inspect `h.activeRequests` (via a test-only accessor or `h.mu`) and assert `len(h.activeRequests) == N`, demonstrating unbounded growth proportional to request volume with no quota/allowlist gate, and assert `mockDon.SendToNode` was called N × len(donConfig.Members) times, confirming repeated fan-out to DON nodes per unauthenticated request.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L700-724)
```go
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

**File:** core/services/gateway/gateway.go (L218-273)
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
```

**File:** core/services/gateway/network/httpserver.go (L180-220)
```go
func (s *httpServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.config.CORSEnabled {
		origin := r.Header.Get("Origin")
		if s.isAllowedOrigin(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}

		// handle preflight requests
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
	}

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
	duration := time.Since(startTime)
```
