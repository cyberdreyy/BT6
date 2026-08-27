### Title
Unbounded active-request map growth via unauthenticated `PublicKeyGet` requests to the Vault gateway handler - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
The Taiko report describes an attacker cheaply and repeatedly triggering a state-mutating action (contest) that is never rejected once a cooldown expires, causing state to accumulate in a fixed-size structure (the block ring buffer) until legitimate operations (new proposals) are blocked. The closest reachable analog in this codebase is the Vault gateway handler's `activeRequests` map, which grows on every incoming JSON-RPC request and is *not* bounded by any max-size check (unlike the sibling `handlers/common.requestCache`, which explicitly enforces `maxCacheSize`). Critically, one method — `MethodPublicKeyGet` — is explicitly exempted from authorization, meaning any unprivileged client reaching the gateway's vault endpoint can drive this growth without ever passing the vault's authorizer/allowlist/JWT checks.

### Finding Description
`handler.HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` and explicitly skips authorization: [1](#0-0) 

For every incoming request (including these unauthenticated `PublicKeyGet` requests that miss the aggressive local cache), `newActiveRequest` inserts an entry into `h.activeRequests` keyed by the caller-supplied `req.ID`, with the only guard being a uniqueness check — there is no cap on the total number of entries: [2](#0-1) 

Compare this to the general-purpose `RequestCache` used elsewhere in the gateway, which enforces a hard `maxCacheSize` and rejects new requests once full: [3](#0-2) 

The vault handler has no equivalent bound on `activeRequests`. Entries are only removed either when a quorum response arrives from DON nodes, or by the periodic reaper `removeExpiredRequests`, which runs every 5 seconds and evicts entries older than `requestTimeout` (default 30s): [4](#0-3) [5](#0-4) 

This mirrors the structural pattern in the report: a cheap, repeatable, unprivileged action (here: sending unique-ID `PublicKeyGet` requests) creates entries that persist for a fixed window (the 30s timeout, analogous to the cooldown) before being reclaimed, while nothing in the handler itself throttles the *rate* of insertion. As long as an attacker submits new requests with unique `req.ID` values faster than the 30-second timeout/5-second sweep can reclaim them, `activeRequests` grows without any upper bound — unlike the ring buffer in the Taiko bug, there is no eventual hard rejection at all, so the map can grow indefinitely, consuming gateway memory and potentially causing the vault handler to degrade or crash, denying vault-request availability to legitimate DON members/users.

Note on the `Config` struct: only `NodeRateLimiter` exists, and it is applied solely to *node responses* arriving over `HandleNodeMessage`, not to incoming user requests via `HandleJSONRPCUserMessage`: [6](#0-5) [7](#0-6) 

I was unable to fully confirm within the available context whether an outer layer (e.g. the gateway's HTTP/WebSocket ingress, `core/services/gateway/gateway.go`, or `multihandler.go`) applies a global per-connection or per-IP rate limit in front of `HandleJSONRPCUserMessage` for the vault handler specifically; the `v2` capabilities handler has its own `globalNodeRateLimiter`/`userRateLimiter`, but no equivalent construct was found wired into `handlers/vault/handler.go`'s `Config` or call path.

### Impact Explanation
Uncontrolled growth of `activeRequests` is a memory-exhaustion / availability risk for the gateway process hosting the vault handler. If exploitable at volume, it could degrade or crash the gateway, denying legitimate vault secret operations (`SecretsCreate/Update/Delete/List`) for all DON members and workflow owners routed through that gateway instance — a shared, security-relevant service (vault secrets). This is lower severity than the original Taiko finding (which stalled all rollup activity for a fixed cost) because here the impact is local resource exhaustion of a single gateway handler rather than global consensus DoS, and mitigating factors (aggressive public-key caching, 30s/5s reap cycle) reduce steady-state accumulation. Still, the complete absence of a maximum-size guard is a structural gap relative to the sibling `RequestCache`, which explicitly hardens against this exact class of issue.

### Likelihood Explanation
The `MethodPublicKeyGet` path requires no authentication/authorization, so any client able to reach the gateway's vault JSON-RPC endpoint qualifies as "unprivileged." The attack requires only sending a stream of requests with unique `ID` values and no valid response ever arriving (or deliberately using an ID that will never resolve to quorum), which is trivial to script. The main uncertainty is whether some outer rate limiter (not found in the reviewed code) throttles the request rate before it reaches this handler, which I could not conclusively rule out or confirm.

### Recommendation
- Add an explicit `maxActiveRequests` (or similar) bound to the vault `handler`, mirroring `handlers/common.requestCache`'s `maxCacheSize` check in `newActiveRequest`, rejecting new requests once the limit is reached.
- Apply a per-caller (and/or global) rate limiter to `HandleJSONRPCUserMessage`, not just to `HandleNodeMessage`'s node-response path, especially for methods that bypass authorization such as `MethodPublicKeyGet`.
- Consider requiring at least a lightweight proof-of-origin (e.g., minimal signature or session token) even for `PublicKeyGet` requests that miss the cache, or serve cache-miss fallbacks from a single in-flight request (de-duplicated/coalesced) rather than allowing unlimited concurrent distinct entries.

### Proof of Concept
Conceptual (not executed against a live system):
1. Identify a gateway endpoint hosting the Vault handler configured with `vaultcap`/`vault.NewHandler`.
2. Force cache misses for `PublicKeyGet` (e.g., race the first request before the public key is cached, or target a gateway instance where `cachedPublicKeyGetResponse` is nil) — see the bypass in `HandleJSONRPCUserMessage`: [8](#0-7) 
3. Send a high-rate stream of `MethodPublicKeyGet` JSON-RPC requests, each with a distinct, unique `req.ID` (up to 200 characters, per the only length check present): [9](#0-8) 
4. Each request inserts a new unbounded entry into `activeRequests` via `newActiveRequest`, which has no size cap: [2](#0-1) 
5. If requests are generated faster than the 5-second sweep can reap 30-second-old entries, `activeRequests` grows unbounded, consuming gateway memory over time.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L178-184)
```go
// Config configures the gateway-side Vault handler.
type Config struct {
	NodeRateLimiter   ratelimit.RateLimiterConfig `json:"nodeRateLimiter"`
	RequestTimeoutSec int                         `json:"requestTimeoutSec"`
	Auth0             *vaultcap.Auth0Config       `json:"auth0,omitempty"`
}

```

**File:** core/services/gateway/handlers/vault/handler.go (L287-308)
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
		}()
		return nil
	})
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

**File:** core/services/gateway/handlers/common/requestcache.go (L46-76)
```go
func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

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
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```
