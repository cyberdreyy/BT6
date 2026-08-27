### Title
Unbounded growth of the Vault gateway handler's `activeRequests` map via unauthenticated/unprivileged JSON-RPC requests - (File: `core/services/gateway/handlers/vault/handler.go`)

### Summary
The gateway-side Vault handler (`core/services/gateway/handlers/vault/handler.go`) stores every incoming user JSON-RPC request in an in-memory map, `h.activeRequests`, keyed by the client-supplied `req.ID`, with no upper bound on the number of concurrent entries. This is analogous to the reported `onERC721Received` issue: any caller can cheaply add distinct entries to a stateful collection, and the cost of maintaining/cleaning that collection scales with the number of entries an attacker chooses to create.

### Finding Description
`newActiveRequest` inserts a new `*activeRequest` into `h.activeRequests[req.ID]` after only checking that the same ID hasn't already been used — there is no limit on the total number of entries in the map: [1](#0-0) 

This differs from the sibling `RequestCache` implementation used by other gateway handlers, which explicitly enforces `maxCacheSize` and rejects new requests once the cache is full: [2](#0-1) 

The Vault handler's `activeRequests` map has no such cap. Entries are only pruned lazily by a periodic goroutine (`removeExpiredRequests`) that runs every `defaultCleanUpPeriod` (5 seconds) and does a full O(n) scan while holding a lock: [3](#0-2) [4](#0-3) 

Because entries live for the full `requestTimeout` (default 30s per `Config.RequestTimeoutSec`) before being cleaned up, an unprivileged client can send a burst of requests with unique `ID` values (a JSON-RPC request field entirely controlled by the caller) faster than the cleanup goroutine can prune them, causing the map to grow proportionally to attacker-supplied request volume for up to 30 seconds at a time, repeated indefinitely. [5](#0-4) 

### Impact Explanation
Every incoming message (regardless of ultimate authorization outcome) results in a heap allocation and a map insertion that is not size-bounded, unlike the codebase's own `RequestCache` pattern which explicitly guards against this via `maxCacheSize`. Sustained bursts of low-cost, uniquely-ID'd requests inflate `h.activeRequests`, increasing lock contention (the map is guarded by `h.mu`, touched on every request, response, and the periodic sweep) and per-cycle cleanup cost, degrading throughput/latency for legitimate Vault requests processed by the same DON-facing gateway handler. This is a resource-exhaustion / availability degradation on the gateway component, not a fund-loss or credential-disclosure bug, so the severity is best characterized as a DoS-class issue rather than "High" in the ERC721 report's sense (there, growth directly cost the victim gas to clean up; here, growth costs the node/gateway operator resources and can degrade service for other users).

### Likelihood Explanation
Likelihood is moderate: the vulnerable code path is reachable by any client of the gateway (not just DON nodes) sending JSON-RPC requests, and `req.ID` is fully attacker-controlled and used directly as a map key with only an equality check against existing entries — no authentication is required simply to have an entry created in this map (authentication happens later, deeper in request processing for state-changing methods). Actual exploitability depends on how the gateway's outer HTTP/WebSocket layer rate-limits or authenticates inbound connections before reaching `HandleJSONRPCUserMessage`/`newActiveRequest`; I was not able to fully trace that outer entry point within the available context, so this should be verified before treating it as conclusively exploitable at scale.

### Recommendation
- Add an explicit maximum size to `h.activeRequests`, mirroring the `maxCacheSize` pattern already used in `core/services/gateway/handlers/common/requestcache.go`, and reject/queue new requests once the cap is reached.
- Consider per-sender/per-connection quotas so a single unauthenticated caller cannot monopolize the shared `activeRequests` map.
- Shrink `defaultCleanUpPeriod`/`requestTimeout` or make cleanup incremental/O(1) per request (e.g., a time-ordered eviction structure) rather than a periodic full-map scan, to bound worst-case memory/lock-hold time under load.

### Proof of Concept
Not executed (static analysis only). Conceptually: an attacker able to reach the gateway's Vault JSON-RPC endpoint sends a high-rate stream of otherwise-valid-looking JSON-RPC requests (e.g., `MethodPublicKeyGet` or any supported method), each with a freshly generated unique `ID`. Each request causes `newActiveRequest` (`core/services/gateway/handlers/vault/handler.go:466-481`) to insert an entry into `h.activeRequests` with no capacity check; entries persist until the 5-second-interval `removeExpiredRequests` sweep and the 30-second default timeout elapse, allowing sustained growth of the map under continuous request pressure.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L212-226)
```go
func newHandlerWithAuthorizer(methodConfig json.RawMessage, donConfig *config.DONConfig, don gwhandlers.DON, capabilitiesRegistry capabilitiesRegistry, authorizer vaultcap.Authorizer, jwtAuth services.Service, lggr logger.Logger, clock clockwork.Clock, limitsFactory limits.Factory) (*handler, error) {
	var cfg Config
	if err := json.Unmarshal(methodConfig, &cfg); err != nil {
		return nil, fmt.Errorf("failed to unmarshal method config: %w", err)
	}

	if cfg.RequestTimeoutSec == 0 {
		cfg.RequestTimeoutSec = 30
	}

	nodeRateLimiter, err := ratelimit.NewRateLimiter(cfg.NodeRateLimiter)
	if err != nil {
		return nil, fmt.Errorf("failed to create node rate limiter: %w", err)
	}

```

**File:** core/services/gateway/handlers/vault/handler.go (L279-308)
```go
func (h *handler) Start(_ context.Context) error {
	return h.StartOnce("VaultHandler", func() error {
		h.lggr.Debug("starting vault handler")
		if h.jwtAuth != nil {
			if err := h.jwtAuth.Start(context.Background()); err != nil {
				return fmt.Errorf("failed to start JWTBasedAuth: %w", err)
			}
		}
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

**File:** core/services/gateway/handlers/common/requestcache.go (L46-66)
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
```
