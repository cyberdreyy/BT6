### Title
No per-caller admission control before `newActiveRequest`/`fanOutToNodes` allows unbounded `activeRequests` growth and node fan-out amplification - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` only validates that `req.ID` is non-empty, ≤200 chars, and not already in use before calling `newActiveRequest` (which allocates a map entry) and `fanOutToNodes` (which sends the request to every DON member). There is no caller-identity-based rate limit, quota, or authentication check anywhere in this path, and the gateway's `ProcessRequest` entrypoint in `core/services/gateway/gateway.go` forwards to `HandleJSONRPCUserMessage` without any per-sender throttling either.

### Finding Description
The request path is: HTTP request → `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) → `multiHandler.HandleJSONRPCUserMessage` (`core/services/gateway/multihandler.go:62-69`) → `handler.HandleJSONRPCUserMessage` (`core/services/gateway/handlers/confidentialrelay/handler.go:349-366`).

In `gateway.ProcessRequest`, the only checks are request decoding, an ID length cap of 200 chars, and (for legacy requests only) `msg.Validate()` — there is no rate limiter or per-sender quota invoked before dispatching to the handler [1](#0-0) .

Inside `handler.HandleJSONRPCUserMessage`, the only checks are ID non-emptiness and length; there is no authentication of the caller, no lookup of a per-sender/per-identity rate limiter, and no cap on the number of concurrent entries in `h.activeRequests` [2](#0-1) . `newActiveRequest` only rejects a duplicate `req.ID`, not a caller-level quota, and unconditionally inserts into `h.activeRequests` [3](#0-2) . `fanOutToNodes` then sends the message to every DON member [4](#0-3) .

The existing `globalNodeRateLimiter` and `perNodeRateLimiters` are only consulted in `HandleNodeMessage` (the inbound node-response path), gating responses from DON nodes back to the gateway — not the user-request admission path [5](#0-4) . Confirmed by grepping the file: `globalNodeRateLimiter`/`perNodeRateLimiters` never appear inside `HandleJSONRPCUserMessage`, `newActiveRequest`, or `fanOutToNodes`.

Because each distinct `req.ID` is accepted (up to 200 bytes each, unbounded in count), an attacker who can reach the gateway's JSON-RPC user endpoint can repeatedly submit unique IDs, each of which: (1) allocates a permanent `activeRequest` entry (map + mutex + response map) that persists until `requestTimeout` (default 30s) expires via the cleanup goroutine, and (2) triggers a fan-out send to every node in the DON. A sustained request rate exceeding what the 30-second expiry sweep can drain will cause `h.activeRequests` to grow without bound, and correspondingly multiply outbound sends to all DON members.

### Impact Explanation
This matches a resource-exhaustion / denial-of-service impact class: an unauthenticated or low-privileged caller can grow gateway-side memory (unbounded map of `activeRequest` objects, each holding buffers and mutexes) and amplify outbound traffic to every DON node, degrading or denying the confidential-relay service for other legitimate callers of the same gateway instance. It is a single-identity-driven exhaustion of a shared resource (the DON connection and processing capacity), not a targeted key/secret/fund compromise — bounded to gateway/service availability degradation, which is a lower-severity DoS finding.

### Likelihood Explanation
Preconditions are minimal: any actor able to reach the gateway's JSON-RPC user endpoint for the confidential relay service (no admin/operator/host access needed) can trigger this repeatedly and deterministically by generating unique `req.ID` strings (up to 200 characters, trivially many combinations). The 30-second (default) `requestTimeout` cleanup limits how long any single burst of entries lingers, but does not cap the arrival rate, so a sustained flood at a rate exceeding drain capacity accumulates unbounded state. This is straightforward to reproduce and does not depend on race conditions or privileged network positioning.

### Recommendation
Add per-caller/per-sender admission control before `newActiveRequest`/`fanOutToNodes` in `HandleJSONRPCUserMessage`, e.g., reuse the existing `limits.Factory`/`limits.RateLimiter` pattern already used for `globalNodeRateLimiter`/`perNodeRateLimiters` to create a global and per-sender (keyed by authenticated caller identity, e.g., `req.Auth`/sender address) inbound rate limiter, reject with a `RateLimitExceeded`-style error before allocating an `activeRequest`, and/or enforce a hard cap on the total size of `h.activeRequests` (or per-sender concurrent request count) with rejection once the cap is reached.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a stub `DON` (mock `SendToNode` always returning nil) and a `clockwork.FakeClock`.
2. In a loop of N (e.g., 10,000) iterations, call `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request[json.RawMessage]{ID: uuid.New().String(), Method: MethodSecretsGet, ...}, stubCallback)`.
3. Assert `err == nil` for every call (no rejection/backpressure occurs) and that `len(h.activeRequests)` grows linearly with N (inspect via test-only accessor or exported test helper), demonstrating unbounded growth with no admission control tied to caller identity, before the cleanup goroutine's `requestTimeout` sweep has a chance to run.
4. Additionally assert that `mockDon.SendToNode` was called `N * len(donConfig.Members)` times, showing fan-out amplification with no throttling of the underlying node-send volume.

### Citations

**File:** core/services/gateway/gateway.go (L218-276)
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
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
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
