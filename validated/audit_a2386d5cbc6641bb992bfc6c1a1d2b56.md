### Title
Unmetered per-request fan-out and unbounded `activeRequests` map growth in Confidential Relay gateway handler enables node resource-exhaustion DoS - (File: `core/services/gateway/handlers/confidentialrelay/handler.go`)

### Summary
The `handler.HandleJSONRPCUserMessage` entrypoint for the Confidential Relay gateway handler accepts any JSON-RPC request from an unauthenticated/unprivileged gateway client, and unconditionally inserts a new entry into the in-memory `activeRequests` map and fans it out to every DON member, without any per-request-creation rate limiting or quota. The only rate limiters present (`globalNodeRateLimiter`, `perNodeRateLimiters`) are applied solely on the node-response ingestion path (`HandleNodeMessage`), not on request creation. A background goroutine performs a full linear scan of `activeRequests` every second (`removeExpiredRequests`, `forwardGracedRequests`) under `h.mu`. An attacker can cheaply flood unique request IDs to grow this map without bound, making every periodic sweep progressively more expensive and increasing lock contention with the hot paths (`newActiveRequest`, `getActiveRequest`, `sendResponseAndClearRequest`) that also take `h.mu`.

### Finding Description
`HandleJSONRPCUserMessage` [1](#0-0)  only validates that `req.ID` is non-empty and ≤200 characters before calling `newActiveRequest`, which locks `h.mu` and inserts into the map [2](#0-1) , then fans the request out to every DON member via `fanOutToNodes` [3](#0-2) . Unlike `HandleNodeMessage`, which enforces `perNodeRateLimiters` and `globalNodeRateLimiter` before touching `activeRequests` [4](#0-3) , there is no equivalent quota check gating user-side request creation.

Every second, a background goroutine performs an unbounded linear scan over `activeRequests` twice — once in `forwardGracedRequests` and once in `removeExpiredRequests` — under `h.mu.RLock()` [5](#0-4) [6](#0-5) , mirroring the reported bug class: an unmetered, permissionlessly-triggerable linear iteration over an attacker-inflatable collection that runs on every tick/block regardless of load. Because entries persist for up to `requestTimeout` (default 30s, `defaultRequestTimeoutSec`) before expiry cleanup [7](#0-6) , an attacker can sustain an arbitrarily large working set by continuously submitting unique-ID requests faster than the 30-second expiry, since there is no cap on the number of concurrently active requests.

### Impact Explanation
This allows a single unprivileged client to grow node memory and CPU cost (map growth, per-tick O(n) scans under a shared mutex, and per-request fan-out goroutines to every DON member) without needing credentials, an allowlist entry, or a passing governance/workflow-registry action. Sustained abuse degrades or exhausts the gateway/handler process, which can delay or block legitimate confidential-relay traffic — a resource-exhaustion/availability impact directly analogous to the reported permissionless chain-halt vector.

### Likelihood Explanation
`HandleJSONRPCUserMessage` is reachable by any client capable of sending a JSON-RPC request to the gateway for the `MethodSecretsGet`/`MethodCapabilityExec` methods handled by this component [8](#0-7) , and the only guard is a per-request-ID uniqueness check with the ID reused as the map key. No authentication/authorization step or per-client quota is enforced before an entry is created and fanned out, so the vector is cheap and directly reachable from an unprivileged actor.

### Recommendation
Add a per-client/global rate limiter (or a hard cap on concurrently active requests) on the request-creation path in `HandleJSONRPCUserMessage`/`newActiveRequest`, mirroring the `globalNodeRateLimiter`/`perNodeRateLimiters` pattern already used for node responses, and bound or shard the periodic cleanup scan so its cost cannot grow unbounded with attacker-controlled input.

### Proof of Concept
Not independently executed; based on static analysis of `core/services/gateway/handlers/confidentialrelay/handler.go`. A functional PoC would: (1) send a large volume of JSON-RPC requests with unique `ID`s and valid `Method` values to the confidential relay gateway endpoint within the `requestTimeout` window, without any authentication token; (2) observe `activeRequests` map growth, increasing latency/lock contention on `h.mu`, and rising CPU/goroutine counts from `fanOutToNodes`. This part is unverified — I could not confirm from indexed code alone whether any authentication or per-user quota is enforced upstream (e.g., in the JSON-RPC codec/gateway HTTP layer) before dispatch to this handler; a Devin session with full repo/runtime access would be needed to confirm whether any such gate exists outside the indexed portion of `core/services/gateway/api/jsonrpccodec.go`.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L30-47)
```go
const (
	// defaultCleanUpPeriod is how often expired requests are swept and closed grace
	// windows are forwarded, so it also bounds how far past its deadline a grace
	// window can run.
	defaultCleanUpPeriod = time.Second

	defaultRequestTimeoutSec  = 30
	defaultNodeSendTimeoutSec = 10

	// defaultQuorumGraceMillis bounds the extra wait after quorum is reached. It must
	// stay well below the caller's own HTTP deadline, which is what actually cuts the
	// request short when the DON never produces 2F+1 signed responses.
	defaultQuorumGraceMillis = 10_000

	// Re-exported from chainlink-common for local use and test convenience.
	MethodSecretsGet     = relaytypes.MethodSecretsGet
	MethodCapabilityExec = relaytypes.MethodCapabilityExec
)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L341-343)
```go
func (h *handler) Methods() []string {
	return []string{MethodSecretsGet, MethodCapabilityExec}
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-652)
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

	_ = group.Wait()

	numNodeErrors := nodeErrors.Load()
	remainingPossibleResponses := len(h.donConfig.Members) - int(numNodeErrors)
	if remainingPossibleResponses < h.donConfig.F+1 && numNodeErrors > 0 {
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes")))
	}

	l.Debugw("successfully forwarded request to relay nodes")
	return nil
}
```
