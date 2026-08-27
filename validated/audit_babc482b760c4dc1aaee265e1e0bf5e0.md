### Title
Unauthenticated `MethodPublicKeyGet` requests bypass authorization and can force unlimited `fanOutToVaultNodes` amplification during cache-cold windows - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` intentionally skips all authorization for `vaulttypes.MethodPublicKeyGet`, returning the cached master public key to any caller with zero credentials. Before the key is cached (at gateway startup, or any period where `h.cachedPublicKeyGetResponse` is nil), every distinct request forces a full `fanOutToVaultNodes` broadcast to all DON members with no gateway-side, per-client, or per-request rate limit gating this specific dispatch path.

### Finding Description
In `HandleJSONRPCUserMessage`, the `MethodPublicKeyGet` branch is reached before any authorization check and explicitly documents the design assumption: "Public key requests don't require authorization... Note we cache this value quite aggressively so don't need to worry about DoS." [1](#0-0) 

When `h.getCachedPublicKey()` returns `nil` (true at gateway startup before the first successful fetch, since the periodic refresh ticker only fires after its first 1-minute interval elapses, per `Start`), the handler creates a new `activeRequest` keyed by the caller-supplied `req.ID` and calls `handlePublicKeyGet`, which in turn calls `fanOutToVaultNodes`, unconditionally sending the request to every DON member via `h.don.SendToNode`. [2](#0-1) [3](#0-2) 

The only rate limiter present, `h.nodeRateLimiter`, is applied in `HandleNodeMessage` to throttle inbound *node responses*, not outbound client-triggered fan-out requests, so it does not gate this path at all. [4](#0-3) 

Because `req.ID` uniqueness is only checked against re-use of the same exact ID (`newActiveRequest` errors only on collision), an attacker can supply an unbounded number of unique IDs before the cache warms, each one:
- Creating a new tracked `activeRequest` entry (unbounded map growth pressure), and
- Fanning out a full JSON-RPC request to every node in `h.donConfig.Members`. [5](#0-4) 

There is no gate, quota, or allowlist check (like `writeMethodsEnabled`, used for the write methods) applied to the `MethodPublicKeyGet` path. [6](#0-5) 

### Impact Explanation
This confirms the public key is intentionally exposed with no authorization requirement (not a secret-disclosure issue). However, the "cached quite aggressively so don't need to worry about DoS" comment is an assumption, not an enforced control: during the cold-cache window (gateway/process restart, up to the first periodic refresh or first successful client-triggered fetch), an unauthenticated attacker can force one full DON-wide fan-out per unique request ID, at no rate-limited cost on the ingress side. This matches an unauthenticated resource-exhaustion / quota-bypass impact class against the Vault DON's node-facing transport, though bounded in duration to the cache-cold window per restart.

### Likelihood Explanation
Fully unauthenticated (zero credentials) — matches the stated precondition exactly. The exploit requires only timing the burst of requests to the window between gateway startup and the first successful public key cache population (bounded by up to 1 minute per `tickerVaultPublicKeyRefresh`, or shorter if a legitimate client request warms it sooner). This is a real, restart-triggered window rather than a permanent condition, so exploitability is repeatable but time-boxed to each gateway/process restart.

### Recommendation
Add a lightweight per-source (or global) rate limiter / single-flight de-duplication in the `MethodPublicKeyGet` cache-miss branch of `HandleJSONRPCUserMessage`/`handlePublicKeyGet` so that concurrent cache-miss requests coalesce into a single `fanOutToVaultNodes` call (e.g., using `singleflight` keyed on the method, independent of caller-supplied `req.ID`), rather than triggering one fan-out per unique client-supplied ID.

### Proof of Concept
1. Construct a `handler` via `newHandlerWithAuthorizer` with a `DON` mock (`don.On("SendToNode", ...)`) and leave `cachedPublicKeyGetResponse`/`cachedPublicKeyObject` unset (simulating cold cache after restart).
2. In a loop, call `h.HandleJSONRPCUserMessage(ctx, req, callback)` with `Method: vaulttypes.MethodPublicKeyGet` and N distinct `req.ID` values, no `req.Auth`.
3. Assert `don.SendToNode` was invoked N × `len(donConfig.Members)` times (one full fan-out per unique ID) and that `len(h.activeRequests)` grows linearly with N, with no rejection/backoff at any point — demonstrating absence of a request-level limiter on this path.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L603-616)
```go
func (h *handler) handleSecretsCreate(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	err := h.writeMethodsEnabled.AllowErr(ctx)
	if errors.Is(err, limits.ErrorNotAllowed{}) {
		l.Warnw("secrets write method called but write methods are disabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.UnsupportedMethodError, errors.New("vault write methods(create/update/delete) are disabled: "+err.Error()), nil))
	} else if err != nil {
		l.Errorw("error checking if write methods are enabled", "error", err)
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("error checking if write methods are enabled: "+err.Error()), nil))
	}

	return h.fanOutToVaultNodes(ctx, l, ar)
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
