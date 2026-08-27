### Title
Unvalidated `req.Method` in `HandleJSONRPCUserMessage` allows quota-consuming fan-out to all DON nodes before rejection - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` only validates `req.ID` length/emptiness before creating an `activeRequest` and calling `fanOutToNodes`, which sends the raw request to every DON member via `h.don.SendToNode` [1](#0-0) . There is no check that `req.Method` is one of `h.Methods()` (`MethodSecretsGet`, `MethodCapabilityExec`) before this fan-out occurs [2](#0-1) . Additionally, `gateway.ProcessRequest` calls `h.HandleJSONRPCUserMessage` without checking the method against the handler's declared methods, and `multiHandler.getHandler` has a single-handler bypass that skips the `methodToHandler` lookup entirely when only one handler type is registered [3](#0-2) , so arbitrary method strings reach the confidentialrelay handler unfiltered in that common deployment shape.

### Finding Description
The reachable path is: attacker sends an HTTP/gateway JSON-RPC request with a valid DON/service routing key but `req.Method = "totally.unsupported"` -> `gateway.ProcessRequest` decodes it, validates only ID length, and calls `h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)` without any method allow-list check [4](#0-3) . If routed via `multiHandler` and it is the only registered handler type, `getHandler` returns the handler directly, bypassing the `methodToHandler` map that would otherwise reject unknown methods [5](#0-4) . Inside `confidentialrelay.handler.HandleJSONRPCUserMessage`, only `req.ID` is validated; `req.Method` is never checked against `h.Methods()` [1](#0-0) . The handler then registers an `activeRequest` in `h.activeRequests` and immediately calls `h.fanOutToNodes`, which iterates every member of `h.donConfig.Members` and calls `h.don.SendToNode(sendCtx, node.Address, &ar.req)` for each one [6](#0-5) . No rate limiter or method-quota check gates this outbound fan-out: the `globalNodeRateLimiter`/`perNodeRateLimiters` are only consulted in `HandleNodeMessage` for inbound node responses, not before outbound dispatch [7](#0-6) . This means every unsupported-method request is fully dispatched to all N DON nodes before any node can reject it, and only afterward (when nodes eventually respond with errors, or via bundler/quorum logic) does the request terminate with a fatal/timeout error.

### Impact Explanation
This is a quota/resource exhaustion vector rather than data exposure: an unauthenticated or low-privilege caller of the gateway HTTP API can force the gateway to transmit an unbounded stream of garbage-method requests to every node in a DON, consuming node websocket bandwidth, per-node processing, and the gateway's own `activeRequests` map/goroutine resources (bounded only by `req.ID` uniqueness) purely by varying `req.Method`, since no allow-list gate exists prior to fan-out. This matches a DoS/quota-bypass class of impact (unauthorized consumption of node compute/network resources) rather than a fund-movement or key-disclosure bug.

### Likelihood Explanation
Feasibility is high: the caller needs no special role — any JSON-RPC client able to reach the gateway's `ProcessRequest` HTTP endpoint with a valid DON/service key (routing) can supply any `req.Method` value and a unique `req.ID` per request. Requests are cheap to generate and repeat, and the described `multiHandler` single-handler bypass (or any deployment routing purely by service name/DON ID rather than per-method dispatch) removes the one place a method allow-list could have stopped this before ever reaching the confidentialrelay handler.

### Recommendation
In `handler.HandleJSONRPCUserMessage`, validate `req.Method` against `h.Methods()` (or an internal set) immediately after the ID checks and before calling `h.newActiveRequest`/`h.fanOutToNodes`; return an `UnsupportedMethodError`-style response via the existing `constructErrorResponse(req, api.UnsupportedMethodError, ...)` path without touching `h.don.SendToNode`. Additionally, remove or gate the single-handler bypass in `multiHandler.getHandler` so it still performs the `methodToHandler` lookup (or otherwise ensure callers validate method support before dispatch), and consider rate-limiting outbound fan-out per caller/IP, not just inbound node responses.

### Proof of Concept
Go handler-level test in `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Construct a `handler` with a mocked `gwhandlers.DON` (`don.On("SendToNode", ...)`).
2. Call `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request[json.RawMessage]{ID: "id1", Method: "totally.unsupported"}, callback)`.
3. Assert whether `don.AssertCalled(t, "SendToNode", ...)` is true (currently expected true, confirming quota consumption before rejection) vs. asserting a fast `UnsupportedMethodError` callback response with zero `SendToNode` calls (desired fixed behavior).
4. Repeat with `Method: MethodSecretsGet` as a control to confirm supported methods still dispatch normally.

### Citations

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

**File:** core/services/gateway/multihandler.go (L80-96)
```go
func (m *multiHandler) getHandler(method string) (handlers.Handler, error) {
	// If there's only one handler, return it directly.
	// This preserves backwards compatibility for cases where the method
	// isn't specified on responses (and for cases where only one handler is registered more generally).
	if len(m.typeToHandler) == 1 {
		for _, handler := range m.typeToHandler {
			return handler, nil
		}
	}

	handler, ok := m.methodToHandler[method]
	if !ok {
		return nil, errors.New("no handler found for method " + method)
	}

	return handler, nil
}
```

**File:** core/services/gateway/gateway.go (L264-273)
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
```
