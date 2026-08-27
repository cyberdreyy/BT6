### Title
Unbounded `activeRequests` queue in the ConfidentialRelay gateway handler enables unauthenticated DoS - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
The gateway's ConfidentialRelay handler accepts every incoming JSON-RPC user request and inserts it into an in-memory `activeRequests` map with no authentication, authorization, or per-caller/global rate limiting on the *request intake* path. Rate limiting only exists on the node-response path, not on the user-request path, so an unprivileged client can flood the gateway with unique request IDs to grow this map without bound, mirroring the reported "flood the relayer's internal queue" DoS bug class.

### Finding Description
`HandleJSONRPCUserMessage` is the entry point for user-submitted JSON-RPC requests to this gateway handler. It only validates that `req.ID` is non-empty and ≤200 characters, then immediately calls `h.newActiveRequest(req, callback)`, which inserts the request into `h.activeRequests[req.ID]` and fans it out to all DON nodes: [1](#0-0) 

`newActiveRequest` performs no authentication/authorization and no capacity check — it only rejects duplicate IDs, but since the caller fully controls `req.ID`, this is trivial to avoid by using unique IDs each time: [2](#0-1) 

By contrast, the only rate limiters constructed for this handler (`globalNodeRateLimiter`, `perNodeRateLimiters`) are applied exclusively in `HandleNodeMessage`, i.e., when DON nodes respond back — not when users submit requests: [3](#0-2) [4](#0-3) 

The multiplexer that routes gateway traffic to this handler (`multiHandler.HandleJSONRPCUserMessage`) performs no additional gating — it simply dispatches by method name to the underlying handler: [5](#0-4) 

This is a direct analog to the reported bug class: an unprivileged/unauthenticated client can submit an unbounded stream of distinct-ID requests through the internet-facing gateway, each of which is unconditionally accepted into the handler's internal pending-request queue/map and triggers a fan-out send to every DON node (`fanOutToNodes`), consuming gateway memory and node bandwidth/processing regardless of legitimacy — exactly the "relayer queue filled with malicious requests, legitimate user must wait" scenario from the report.

For comparison, the sibling Vault gateway handler requires `requestProcessor.ProcessRequest` (allowlist/JWT authorization) to succeed *before* calling `newActiveRequest`, which at least gates queue insertion behind authorization: [6](#0-5) 
The ConfidentialRelay handler has no equivalent authorization or rate-limiting gate on its user-request path.

### Impact Explanation
Any unauthenticated/unprivileged caller able to reach the gateway's ConfidentialRelay handler can grow `activeRequests` without bound and force fan-out sends to every DON node for each fabricated request, exhausting gateway memory, node-connector bandwidth, and processing capacity. Legitimate requests queued/processed concurrently would face resource contention and delayed responses (only cleared eventually via `removeExpiredRequests` after `requestTimeout`), matching the report's "Alice has to wait an undefined amount of time" scenario. This is a service-availability/DoS impact against the gateway and the DON it fans out to.

### Likelihood Explanation
Likelihood is high in principle: the request-intake path has no authentication or per-caller/global rate limiter, and the only defense (rejecting duplicate `req.ID`) is trivially bypassed by generating new unique IDs. The main mitigating unknown is whatever the deployment's outer transport layer (e.g., mTLS gateway connector, network-level access control) enforces before a message reaches `HandleJSONRPCUserMessage` — that boundary is outside this handler's code and was not fully verifiable from the indexed files.

### Recommendation
- Add an authorization/allowlist gate (similar to the Vault handler's `requestProcessor.ProcessRequest`) or a per-sender/global rate limiter on `HandleJSONRPCUserMessage` before inserting into `activeRequests` and before fanning out to nodes.
- Bound `activeRequests` size (e.g., max pending requests per sender/global) and reject/backpressure new requests once the limit is reached, independent of request-ID uniqueness.
- Apply the existing rate-limiter pattern used for node responses symmetrically to user-request intake.

### Proof of Concept
1. An unauthenticated client connects to the gateway endpoint serving the ConfidentialRelay handler's methods (`MethodSecretsGet`, `MethodCapabilityExec`).
2. The client repeatedly sends JSON-RPC requests with unique `req.ID` values (e.g., UUIDs) and arbitrary/garbage `Params`.
3. Each request passes the only checks present (`ID` non-empty, ≤200 chars) and is inserted into `h.activeRequests`, then fanned out to every DON member via `fanOutToNodes`, since no authorization or intake rate limiter exists on this path (`core/services/gateway/handlers/confidentialrelay/handler.go:349-383`).
4. Repeating this at high volume grows the in-memory map unbounded until `requestTimeout` expiry and consumes node-connector send bandwidth/processing, delaying or starving legitimate concurrent requests — reproducing the reported DoS.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L232-244)
```go
	globalNodeRateLimiter, err := limitsFactory.MakeRateLimiter(cresettings.Default.GatewayConfidentialRelayGlobalRate)
	if err != nil {
		return nil, fmt.Errorf("failed to create global node rate limiter: %w", err)
	}

	perNodeRateLimiters := make(map[string]limits.RateLimiter, len(donConfig.Members))
	for _, member := range donConfig.Members {
		rl, makeErr := limitsFactory.MakeRateLimiter(cresettings.Default.GatewayConfidentialRelayPerNodeRate)
		if makeErr != nil {
			return nil, fmt.Errorf("failed to create per-node rate limiter for %s: %w", member.Address, makeErr)
		}
		perNodeRateLimiters[member.Address] = rl
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L391-406)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		l.Debug("global relay rate limit exceeded")
		return nil
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

**File:** core/services/gateway/handlers/vault/handler.go (L431-450)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
	if activeRequestErr != nil {
		return activeRequestErr
	}
```
