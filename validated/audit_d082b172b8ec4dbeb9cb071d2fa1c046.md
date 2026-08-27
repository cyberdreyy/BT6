### Title
Confidential relay handler fans out unsupported-method requests to all DON nodes before per-method validation, enabling attacker-triggered DON compute waste - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`(*handler).HandleJSONRPCUserMessage` reserves an `activeRequests` slot and unconditionally calls `fanOutToNodes`, which sends the raw request to every node in `h.donConfig.Members`, without first checking `req.Method` against the supported set (`MethodSecretsGet`, `MethodCapabilityExec`). The method is only validated later, in `bundler.Bundle`'s `default` case, which fires after a node responds or the request expires — by which point the fan-out to the whole DON has already occurred.

### Finding Description
`HandleJSONRPCUserMessage` [1](#0-0)  only validates `req.ID` length/emptiness, then calls `h.newActiveRequest` (consuming a slot in `h.activeRequests`) and immediately `h.fanOutToNodes`, with no check against `h.Methods()` (`MethodSecretsGet`, `MethodCapabilityExec`) [2](#0-1) . `fanOutToNodes` sends `&ar.req` to every member of `h.donConfig.Members` over the node websocket connection unconditionally [3](#0-2) .

Method validation only happens in `bundler.Bundle`'s `default` branch, which returns `errUnknownMethod` [4](#0-3) , and this function is invoked only from `HandleNodeMessage` (after a node actually responds), `removeExpiredRequests` (after timeout), or `forwardAfterGrace` — all *after* fan-out already occurred.

At the routing layer, `gateway.ProcessRequest` dispatches by service name to `serviceToMultiHandler[serviceName]` [5](#0-4) , and `multiHandler.getHandler` explicitly bypasses the `methodToHandler` allowlist when only one handler is registered for a service: *"If there's only one handler, return it directly... preserves backwards compatibility"* [6](#0-5) . Since the confidential relay handler is typically the sole handler for its service, this means an attacker-supplied `req.Method` never has to match `h.Methods()` to reach `HandleJSONRPCUserMessage` — any arbitrary method string reaches the handler and triggers fan-out before being rejected.

### Impact Explanation
An unauthenticated/low-privileged gateway client can send a JSON-RPC request with an arbitrary unsupported method and cause the gateway to broadcast it to every node in the DON (`h.donConfig.Members`), consuming node websocket bandwidth and node-side processing for a request that is guaranteed to fail. This is a DON compute/resource-waste amplification vector (1 attacker request → N node deliveries) tied to the subscription/DON referenced by `donConfig`, matching a low/medium "denial of service / resource exhaustion" bounty class rather than a fund-loss or key-disclosure class. It does not by itself leak secrets or bypass signature/quorum verification (the enclave remains the trust anchor per the `bundler.go` doc comment), so impact is limited to wasted compute/availability degradation, not compromise.

### Likelihood Explanation
No special privilege is required — only the ability to send a JSON-RPC user request to the gateway HTTP endpoint with the correct `serviceName` prefix and any `req.ID` under 200 chars. The request is fully attacker-controlled and repeatable per unique `req.ID` (duplicate IDs are rejected by `newActiveRequest`, but new IDs are trivial to generate) [7](#0-6) . There is no visible per-user rate limiter gating `HandleJSONRPCUserMessage` fan-out in the reviewed handler (the two rate limiters present, `globalNodeRateLimiter` and `perNodeRateLimiters`, are applied only in `HandleNodeMessage` for inbound node responses, not on the outbound user-triggered fan-out path) [8](#0-7) . This makes the issue trivially and repeatably reachable.

### Recommendation
Add a method allowlist check in `HandleJSONRPCUserMessage` before calling `newActiveRequest`/`fanOutToNodes`, rejecting any `req.Method` not in `{MethodSecretsGet, MethodCapabilityExec}` with an immediate error response (mirroring the `errUnknownMethod` check currently only present in `bundler.Bundle`). Additionally, close the `multiHandler.getHandler` single-handler bypass so that method validation against `h.Methods()` is enforced even when only one handler is registered for a service.

### Proof of Concept
Handler-level integration test plan (Go):
1. Construct a `handler` with a `mocks.DON` (mock `SendToNode`) and a `donConfig` with N members, minimal `F`.
2. Call `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request{ID:"x", Method:"bogus.method"}, callback)`.
3. Assert `don.SendToNode` was invoked once per DON member (fan-out occurred) — confirming resource waste — despite `"bogus.method"` not being in `h.Methods()`.
4. Assert the eventual response (via callback, either from expiry sweep or a synthetic node reply) surfaces `errUnknownMethod` only after fan-out, proving validation happens post-fan-out.
5. Contrast with a proposed fix: add a pre-fan-out method check and assert `SendToNode` is never called for an unsupported method, with the callback receiving an immediate error instead.

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

**File:** core/services/gateway/handlers/confidentialrelay/bundler.go (L161-163)
```go
	default:
		return nil, fmt.Errorf("%w: %q", errUnknownMethod, req.Method)
	}
```

**File:** core/services/gateway/gateway.go (L235-272)
```go
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
