Based on the investigation, the reachable analog is in the confidential relay gateway handler's user-request path, which builds an unbounded, rate-limit-free map of pending requests that is iterated under lock every second — closely mirroring the reported bug class (unbounded FIFO/queue growth caused by unthrottled unprivileged submissions, later processed by an O(n) routine that can degrade the whole service).

### Title
Unbounded, unrate-limited `activeRequests` map in the confidential relay gateway handler enables a denial-of-service via request-ID spam - (File: `core/services/gateway/handlers/confidentialrelay/handler.go`)

### Summary
`HandleJSONRPCUserMessage`, the entry point for unprivileged user requests routed through the gateway's `ProcessRequest`, inserts a new `activeRequest` into `h.activeRequests` for every distinct request ID without any rate limiting, quota, or capacity bound on the number of concurrently tracked requests.

### Finding Description
The gateway `ProcessRequest` [1](#0-0)  dispatches unauthenticated/unprivileged client JSON-RPC requests to `h.HandleJSONRPCUserMessage`. For the confidential relay handler, this method only validates that `req.ID` is non-empty and ≤200 characters, then calls `newActiveRequest`, which unconditionally inserts the request into the `activeRequests` map keyed by request ID: [2](#0-1) .

Unlike `HandleNodeMessage` (the node-facing path), which is protected by `globalNodeRateLimiter` and per-node `perNodeRateLimiters` [3](#0-2) , there is no equivalent rate limiter or concurrency cap applied to `HandleJSONRPCUserMessage`. Any caller can therefore submit an unbounded number of distinct request IDs (each request ID must be unique or insertion is rejected, but an attacker fully controls the IDs it submits) to grow `activeRequests` without bound until the periodic cleanup sweep (`defaultCleanUpPeriod = 1s`) expires them after `requestTimeout` (default 30s) [4](#0-3) .

This periodic sweep — `forwardGracedRequests` and `removeExpiredRequests` — iterates the entire map under `h.mu` every second: [5](#0-4) [6](#0-5) . As the map grows from sustained spam, the O(n) sweep work and lock hold time increase proportionally. Because `newActiveRequest`, `getActiveRequest`, and `sendResponseAndClearRequest` all serialize on the same `h.mu` [7](#0-6) [8](#0-7) , an attacker sustaining a high rate of unique-ID submissions can inflate the sweep cost and lock contention, degrading or starving legitimate `HandleNodeMessage` node-response processing and new user request admission for the whole DON-facing confidential relay service — the same bug class as the reported unstake-queue spam (unbounded queue growth from a cheap, unthrottled unprivileged action, later processed by an O(n) routine that everyone else depends on).

### Impact Explanation
A sustained flood of distinct-ID confidential relay requests grows `activeRequests` without bound between sweep cycles, increasing per-sweep O(n) work and `h.mu` lock hold time. This can delay or block legitimate `HandleNodeMessage` calls (which need the same mutex to look up/update/delete entries) and new user request admission, degrading availability of the confidential relay gateway path used to fan out capability/secrets-relay requests to the DON.

### Likelihood Explanation
The `HandleJSONRPCUserMessage` path is reachable from any unauthenticated caller of the gateway's public HTTP/WS entry point via `ProcessRequest`, requiring only a unique `req.ID` string (≤200 chars) per call — a trivial cost for an attacker, and there is no per-sender or global rate limiter guarding this specific admission path (unlike the analogous node-message path).

### Recommendation
Apply a global (and/or per-sender/IP) rate limiter or admission quota to `HandleJSONRPCUserMessage` before inserting into `activeRequests`, similar to the `globalNodeRateLimiter`/`perNodeRateLimiters` already used for node messages, and/or cap the maximum size of `activeRequests`, rejecting new requests once the cap is reached.

### Proof of Concept
1. An unprivileged client repeatedly calls the gateway's public JSON-RPC endpoint targeting the confidential relay service/method, each time with a freshly generated unique `req.ID`.
2. Each call reaches `HandleJSONRPCUserMessage` → `newActiveRequest`, adding an entry to `h.activeRequests` [9](#0-8) , with no rate limiting rejecting the call.
3. Sustaining a rate faster than the 30s expiry combined with the 1s sweep interval causes `activeRequests` to grow continuously, increasing lock-held iteration cost in `removeExpiredRequests`/`forwardGracedRequests` and contending with `h.mu` used by the node-response path, degrading gateway responsiveness for legitimate DON traffic.

### Citations

**File:** core/services/gateway/gateway.go (L218-272)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-389)
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

func (h *handler) getActiveRequest(requestID string) *activeRequest {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.activeRequests[requestID]
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L659-669)
```go
func (h *handler) sendResponseAndClearRequest(ctx context.Context, ar *activeRequest, payload gwhandlers.UserCallbackPayload) error {
	if !ar.completed.CompareAndSwap(false, true) {
		// Another path already answered this request.
		return nil
	}

	sendErr := ar.SendResponse(payload)

	h.mu.Lock()
	delete(h.activeRequests, ar.req.ID)
	h.mu.Unlock()
```
