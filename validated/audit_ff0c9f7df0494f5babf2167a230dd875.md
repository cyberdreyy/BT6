### Title
Unauthenticated `vault.publicKeyGet` requests bypass authorization and are unmetered by any rate limiter, enabling cheap gateway/DON flooding - (File: `core/services/gateway/handlers/vault/handler.go`)

### Summary
The Vault gateway handler's `HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` to skip the normal authorization/validation pipeline (`requestProcessor.ProcessRequest`) that all other vault methods (`SecretsCreate/Update/Delete/List`) must pass through. This bypass is not compensated by any per-sender or global rate limiter on the user-facing path, so an unauthenticated caller can flood the gateway with unique `publicKeyGet` requests, each of which (when the internal cache is momentarily unfilled/stale) is fanned out to every DON member and tracked in an unbounded in-memory map, exactly mirroring the Aleo `split` bug class of a "free"/underpriced request type that bypasses the system's normal cost/authorization controls and crowds out legitimate traffic.

### Finding Description
In `core/services/gateway/handlers/vault/handler.go`, `HandleJSONRPCUserMessage` routes requests as follows: [1](#0-0) 

For `MethodPublicKeyGet` the code explicitly documents that "Public key requests don't require authorization" and calls `h.newActiveRequest` + `h.handlePublicKeyGet` directly — completely bypassing `h.requestProcessor.ProcessRequest`, which is the only place authorization/allowlist and per-owner protections are enforced for the other (`SecretsCreate`, `SecretsUpdate`, `SecretsDelete`, `SecretsList`) methods: [2](#0-1) 

Unlike the node-facing path, which does have a `nodeRateLimiter` (`ratelimit.RateLimiter`) applied in `HandleNodeMessage`: [3](#0-2) 

there is no equivalent per-sender/global rate limiter applied on the user-submitted `HandleJSONRPCUserMessage` path — only a request-ID length/emptiness check exists: [4](#0-3) 

Each incoming `publicKeyGet` request (with a fresh, unique `req.ID`) is registered in the handler's `activeRequests` map via `newActiveRequest`, which only rejects requests that reuse an already-pending ID — it does not cap the total number of concurrently pending requests: [5](#0-4) 

If the public key cache happens to be empty or is invalidated, each such request also triggers a fan-out to **every** DON member node (amplification), and stays resident in `activeRequests` until `requestTimeout` (default 30s) via `removeExpiredRequests`: [6](#0-5) [7](#0-6) 

At the gateway HTTP ingestion layer (`core/services/gateway/gateway.go` `ProcessRequest`), there is likewise no per-sender rate limiting before dispatch to `HandleJSONRPCUserMessage` — only a request-ID length check and handler routing: [8](#0-7) 

This is structurally analogous to the reported Aleo issue: `MethodPublicKeyGet` is a "cheap"/free operation (no auth check, no antom fee-equivalent throttling) that is exempt from the dynamic-cost/authorization controls applied to every other request type, allowing it to be used to flood shared resources without the cost scaling with load — just as the fixed-fee `split` transaction could crowd out fee-market-priced transactions.

### Impact Explanation
An unauthenticated, unprivileged network client can send an unbounded volume of `vault.publicKeyGet` JSON-RPC requests with unique IDs. Each request:
1. Skips authorization entirely (unlike every other vault method).
2. Is untracked by any rate limiter on the ingestion path.
3. Is added to an unbounded `activeRequests` map (memory growth) and, absent a warm cache, fanned out to all DON nodes, multiplying load on the workflow DON as well as the gateway.

This can exhaust gateway memory/goroutines and DON node processing capacity, degrading or denying availability of the Vault service for legitimate users (secrets create/update/delete/list), and can also amplify load onto DON member nodes that must each process and respond to the flood.

### Likelihood Explanation
Likelihood is high: the endpoint is reachable by any unauthenticated actor able to reach the gateway's user-facing HTTP endpoint (no signature/JWT/allowlist required for this method by design), requires no special privileges, and the only friction is generating unique request IDs, which is trivial for an attacker.

### Recommendation
Apply a per-sender and/or global rate limiter (similar to `nodeRateLimiter` or the `userRateLimiter` used in the HTTP trigger handler v2) to `MethodPublicKeyGet` requests before they are admitted into `activeRequests` or fanned out to DON nodes. Additionally, bound the size of `activeRequests` (or apply a lighter-weight, non-fan-out synchronous cache-miss handling path) so that unauthenticated callers cannot cheaply trigger unbounded DON-wide fan-out and memory growth.

### Proof of Concept
1. An attacker (no credentials, no allowlist entry) sends many JSON-RPC requests to the gateway's vault handler with `method = "vault.publicKeyGet"` and a fresh unique `id` (e.g., a UUID) on each request.
2. Each request enters `HandleJSONRPCUserMessage` and takes the `MethodPublicKeyGet` branch, skipping `requestProcessor.ProcessRequest` (`core/services/gateway/handlers/vault/handler.go:413-429`).
3. If `getCachedPublicKey()` returns `nil` (e.g., right after startup, before the periodic 1-minute refresh populates the cache, or during any window where the cache is stale), each request calls `newActiveRequest` (registering unboundedly in `h.activeRequests`) and `handlePublicKeyGet`, which calls `fanOutToVaultNodes`, sending the request to every DON member (`handler.go:726-742`).
4. Because no rate limiter guards this path, the attacker can repeat step 1 at high volume, growing `activeRequests` unbounded and multiplying load on all DON nodes, without ever needing valid authorization.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L431-443)
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

**File:** core/services/gateway/gateway.go (L218-262)
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
```
