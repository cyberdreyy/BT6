### Title
Unbounded per-request memory allocation in Gateway Confidential Relay handler enables unprivileged-client DoS via unbounded `activeRequests` map - (File: core/services/gateway/handlers/confidentialrelay/handler.go)

### Summary
The Chainlink Gateway's HTTP frontend (`ProcessRequest`) enforces only a per-message byte-size cap (`MaxRequestBytesLimiter`) and a per-message ID length cap, but applies **no rate limit or concurrency cap on the number of distinct in-flight user requests** before dispatching into handler-specific state. The `confidentialrelay` handler stores one `activeRequest` struct per unique `req.ID` in an unbounded `map[string]*activeRequest` (`h.activeRequests`), which is only cleaned up after `requestTimeout` (default 30s) elapses or completion. An unauthenticated/unprivileged HTTP client can send an unbounded stream of small, uniquely-IDed JSON-RPC requests to accumulate memory, analogous to the Boxo bitswap `WANT_BLOCK`/`WANT_HAVE` unbounded-queue DoS (CVE-2023-25568), where per-request/peer state was allowed to grow without limit.

### Finding Description
`gateway.ProcessRequest` decodes an inbound JSON-RPC request and, for non-legacy requests, calls `h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)` directly, with no global concurrency limiter and no per-sender rate limiter applied at this layer: [1](#0-0) 

The only guards applied before this call are a payload-size limiter on the raw HTTP body and a 200-character cap on the request ID: [2](#0-1) [3](#0-2) 

In the `confidentialrelay` handler, `HandleJSONRPCUserMessage` calls `newActiveRequest`, which allocates and inserts a new `*activeRequest` into `h.activeRequests` keyed only by the caller-supplied `req.ID`, with no bound on total map size or per-sender count: [4](#0-3) 

Each `activeRequest` retains the full request, a `responses` map, callback state, and remains resident until either completion or the periodic cleanup sweep evicts it after `requestTimeout` (default 30s): [5](#0-4) [6](#0-5) [7](#0-6) 

Rate limiting exists in this handler only for **node-originated** responses (`perNodeRateLimiters`, `globalNodeRateLimiter` in `HandleNodeMessage`), not for the **incoming user request path** (`HandleJSONRPCUserMessage`) that creates the map entries: [8](#0-7) 

This differs from sibling handlers such as the HTTP-trigger handler and vault handler, which explicitly apply a `userRateLimiter`/`checkRateLimit` on the incoming user request before creating any persistent state: [9](#0-8) 

The bug class matches the Boxo advisory precisely: an unprivileged/untrusted caller can drive server-side allocation of long-lived per-request state keyed by attacker-chosen IDs, with no cap on the number of concurrently tracked entries, and the fix pattern in Boxo (`MaxQueuedWantlistEntriesPerPeer`, prompt state cleanup) is the same shape of mitigation missing here (a `MaxActiveRequests` bound or per-sender request-rate limiting on `HandleJSONRPCUserMessage`).

### Impact Explanation
An attacker with only network access to the Gateway's public HTTP endpoint (no node/operator privileges required) can flood it with valid-shaped JSON-RPC requests with unique `req.ID` values. Each request causes fan-out to every DON member via `fanOutToNodes` (bounded per-node by `nodeSendTimeout`) and, more importantly, allocates and retains an `activeRequest` for up to `requestTimeout` (default 30s, configurable). Sustained request volume within that window causes unbounded heap growth proportional to `rate × requestTimeout`, which can exhaust gateway memory, degrade or crash the process, and disrupt confidential-relay service for legitimate workflow nodes/users relying on the same gateway — a availability/DoS impact on unprivileged-reachable node-adjacent infrastructure.

### Likelihood Explanation
Reaching this path requires no authentication beyond what any external caller of the Gateway's public HTTP API already has (the confidential relay method dispatch occurs before any node-side authorization/rate limiting is applied). The only preconditions are: valid JSON-RPC shape, a DON ID/service name that routes to a `confidentialrelay` handler, and unique request IDs (trivially satisfiable). This makes the likelihood high wherever a `confidentialrelay` handler is deployed and reachable from untrusted callers.

### Recommendation
- Add a global and/or per-sender rate limiter (mirroring `userRateLimiter` in the HTTP-trigger/vault handlers) to `HandleJSONRPCUserMessage` in `core/services/gateway/handlers/confidentialrelay/handler.go`, applied before `newActiveRequest` allocates state.
- Enforce a hard cap on `len(h.activeRequests)` (e.g., reject or evict-oldest similar to `pruneCallbacks`/`MaxSavedCallbacks` pattern used in `core/services/gateway/handlers/capabilities/handler.go`) so unbounded growth cannot occur even absent rate limiting.
- Consider reducing `defaultRequestTimeoutSec`/exposing a tighter default for high-throughput deployments, and instrument metrics on active-request count for operational alerting.

### Proof of Concept
1. Configure a Gateway with a `confidentialrelay`-backed DON/service reachable over its public HTTP endpoint.
2. From an unauthenticated client, issue a high-rate stream of JSON-RPC requests to that service's method (e.g. `MethodSecretsGet`/`MethodCapabilityExec`) each with a unique `id` field (up to 200 chars) and a minimal valid payload, without waiting for responses.
3. Each request enters `gateway.ProcessRequest` → `handler.HandleJSONRPCUserMessage` → `newActiveRequest`, inserting an entry into `h.activeRequests` that persists for up to `RequestTimeoutSec` (default 30s).
4. Because no rate limiter or size cap gates this path, sending requests faster than the 30s expiry window causes `h.activeRequests` to grow without bound, increasing gateway memory usage; sustained flooding demonstrates memory growth that does not stabilize until the attacker stops.

Note: I was unable to fully verify from the index whether the gateway's outer connection manager / TLS/WS layer imposes any additional global concurrent-connection limit that might partially mitigate this (e.g., in `core/services/gateway/network/wsserver.go` or `connector.go`); a Devin session with full repo access would be needed to confirm there is no independent global request-rate guard elsewhere in the request path. [10](#0-9)

### Citations

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/gateway.go (L264-276)
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

**File:** core/services/gateway/network/httpserver.go (L196-209)
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
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L30-42)
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
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L80-95)
```go
type activeRequest struct {
	req       jsonrpc.Request[json.RawMessage]
	responses map[string]*jsonrpc.Response[json.RawMessage]
	mu        sync.Mutex
	completed atomic.Bool

	// graceStarted is set the first time the request holds F+1 signed responses, so
	// the grace deadline is armed once per request rather than moved forward by every
	// later response. graceDeadline is guarded by mu and is only meaningful once
	// graceStarted is set.
	graceStarted  atomic.Bool
	graceDeadline time.Time

	createdAt time.Time
	gwhandlers.Callback
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L306-339)
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

	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		l := logger.With(h.lggr, "method", er.req.Method, "requestID", er.req.ID)
		l.Debugw("request expired, evaluating collected relay responses",
			"collected", len(responses),
			"nodes", len(h.donConfig.Members),
			"unanswered", len(h.donConfig.Members)-len(responses),
		)
		summary, err := h.bundler.Bundle(er.req, responses, l)
		if err != nil {
			l.Errorw("failed to build relay response bundle", "error", err)
			if sendErr := h.sendResponseAndClearRequest(ctx, er, h.constructErrorResponse(er.req, api.FatalError, err)); sendErr != nil {
				l.Errorw("error returning bundle failure on expiry", "error", sendErr)
			}
			continue
		}
		// Expiry makes further responses unavailable to this request. The common
		// readiness path forwards a viable partial bundle or returns a timeout.
		if err := h.forwardBundleOrTerminateIfReady(ctx, l, er, summary, 0, true); err != nil {
			l.Errorw("error forwarding bundle on expiry", "error", err)
		}
	}
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```

**File:** core/services/gateway/network/wsserver.go (L1-1)
```go
package network
```
