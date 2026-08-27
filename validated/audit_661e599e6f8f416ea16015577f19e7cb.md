### Title
Unbounded growth of `activeRequests` in the Confidential Relay Gateway Handler enables unprivileged-actor DoS via O(n) cleanup loops and DON fan-out amplification - (File: core/services/gateway/handlers/confidentialrelay/handler.go)

### Summary
The Confidential Relay Gateway Handler's `HandleJSONRPCUserMessage` entry point creates a new entry in the `activeRequests` map for every inbound user request, keyed only by the caller-supplied `req.ID`, with no cap on the number of concurrently tracked requests. Two background goroutines (`removeExpiredRequests` and `forwardGracedRequests`) iterate the *entire* `activeRequests` map on every tick of a 1-second ticker. An unprivileged client that floods the gateway with confidential-relay requests (each with a unique ID) can grow this map without bound, turning the per-tick cleanup work into an unbounded, linearly-scaling loop — the same bug class as the Carapace `ProtectionPool` unbounded-array iteration, adapted to a Go service (CPU/memory exhaustion instead of a block gas limit).

### Finding Description
`HandleJSONRPCUserMessage` [1](#0-0)  is the internet/DON-facing entry point that gateway routes to for confidential relay requests. It calls `newActiveRequest`, which unconditionally inserts into the `activeRequests` map with no size check: [2](#0-1) 

Unlike the sibling `requestCache` implementation used elsewhere in the gateway, which explicitly enforces a `maxCacheSize` and rejects new requests once the cache is full: [3](#0-2) 

the confidential relay `handler` struct has no analogous `maxActiveRequests`/size guard field: [4](#0-3) 

Each request also fans out to *every* member of the DON: [5](#0-4) 

and the map is fully scanned once per second by two separate maintenance loops driven off a fixed-interval ticker: [6](#0-5) [7](#0-6) [8](#0-7) 

While `HandleNodeMessage` does apply per-node and global rate limiting to responses coming back from DON nodes [9](#0-8) , no equivalent rate limiter or capacity bound exists on the inbound `HandleJSONRPCUserMessage` path that creates entries in `activeRequests`. The only per-request restriction is a request-ID length check [10](#0-9) , which does not limit request *volume*.

### Impact Explanation
An unprivileged caller able to reach the gateway's confidential relay handler (e.g., any workflow/user permitted to send `MethodSecretsGet`/`MethodCapabilityExec` JSON-RPC requests) can submit a high volume of requests with distinct IDs. Each request:
1. Persists in `activeRequests` for up to `RequestTimeoutSec` (default 30s) before expiry cleanup.
2. Triggers a fan-out `SendToNode` call to every DON member, multiplying attacker-controlled load onto the DON.
3. Is scanned every second by both `removeExpiredRequests` and `forwardGracedRequests`, so the per-tick cost of the maintenance goroutines grows linearly (and, combined with per-request mutex locks and bundler calls, potentially super-linearly) with the number of in-flight requests.

Because there is no cap, the request volume needed to make these loops expensive is limited only by the attacker's ability to send distinct request IDs within the request timeout window, degrading the gateway's ability to process legitimate confidential-relay traffic and consuming DON node bandwidth via fan-out amplification.

### Likelihood Explanation
Likelihood is moderate-to-high: any caller with legitimate access to invoke the confidential relay handler's JSON-RPC methods can trigger this without any privileged role, since the code path requires only a well-formed request with a unique ID and no additional check bounds concurrent in-flight requests. No malicious node, peer, or operator access is required — this is purely an unprivileged-client request path.

### Recommendation
Add an explicit cap on the number of concurrently tracked entries in `activeRequests` (mirroring `requestCache.maxCacheSize` in `core/services/gateway/handlers/common/requestcache.go`), rejecting or rate-limiting new requests once the limit is reached. Additionally, apply a rate limiter (analogous to `globalNodeRateLimiter`/`perNodeRateLimiters` used for node responses) to the inbound `HandleJSONRPCUserMessage` path to bound the rate at which new `activeRequests` entries can be created by a single caller or in aggregate.

### Proof of Concept
1. A caller with access to send confidential relay JSON-RPC requests to the gateway repeatedly invokes `HandleJSONRPCUserMessage` with unique `req.ID` values faster than the `RequestTimeoutSec` (default 30s) expiry.
2. Each call succeeds because `newActiveRequest` has no upper bound check [2](#0-1) , growing `h.activeRequests` unbounded, and each call also fans a request out to every DON member [5](#0-4) .
3. The per-second cleanup ticker's `removeExpiredRequests` and `forwardGracedRequests` calls now iterate an ever-larger map every tick [7](#0-6) [8](#0-7) , increasing per-tick CPU/mutex-contention cost and DON-facing send volume proportionally to attacker-supplied request count.

Note: I was unable to fully verify whether a gateway-level global inbound rate limiter or connection-level allowlist sits in front of this handler and effectively bounds request rate before it reaches `HandleJSONRPCUserMessage`; if such an upstream limiter exists and is tightly configured, it could mitigate this finding. I could not locate one in the code explored.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L174-195)
```go
type handler struct {
	services.StateMachine
	donConfig *config.DONConfig
	don       gwhandlers.DON
	codec     api.JsonRPCCodec
	lggr      logger.Logger
	mu        sync.RWMutex
	stopCh    services.StopChan

	globalNodeRateLimiter limits.RateLimiter
	perNodeRateLimiters   map[string]limits.RateLimiter
	requestTimeout        time.Duration
	nodeSendTimeout       time.Duration
	quorumGrace           time.Duration

	activeRequests map[string]*activeRequest
	metrics        *metrics

	bundler relayBundler

	clock clockwork.Clock
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L269-289)
```go
func (h *handler) Start(_ context.Context) error {
	return h.StartOnce("ConfidentialRelayHandler", func() error {
		h.lggr.Info("starting confidential relay handler")
		go func() {
			ctx, cancel := h.stopCh.NewCtx()
			defer cancel()
			ticker := h.clock.NewTicker(defaultCleanUpPeriod)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.Chan():
					h.forwardGracedRequests(ctx)
					h.removeExpiredRequests(ctx)
				case <-h.stopCh:
					return
				}
			}
		}()
		return nil
	})
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L306-315)
```go
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L537-546)
```go
func (h *handler) forwardGracedRequests(ctx context.Context) {
	h.mu.RLock()
	var graced []*activeRequest
	now := h.clock.Now()
	for _, ar := range h.activeRequests {
		if ar.graceElapsed(now) {
			graced = append(graced, ar)
		}
	}
	h.mu.RUnlock()
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-641)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
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
