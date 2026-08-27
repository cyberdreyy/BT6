### Title
Unbounded `activeRequests` growth via unauthenticated distinct-ID flooding enables gateway memory exhaustion - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`(*handler).HandleJSONRPCUserMessage` in the confidential-relay gateway handler accepts any JSON-RPC request with a unique, attacker-chosen `ID` and unconditionally inserts an `activeRequest` entry into `h.activeRequests` via `newActiveRequest`, with no authorization step and no cap on the number of concurrently pending requests per sender or globally. Because entries are only removed on completion or by the `defaultCleanUpPeriod` (1 second) sweep in `removeExpiredRequests`/`forwardGracedRequests`, an attacker who submits requests faster than the sweep can drain them causes the map (and its retained request/response buffers) to grow without bound, exhausting gateway memory shared by all DON subscribers.

### Finding Description
`HandleJSONRPCUserMessage` (`core/services/gateway/handlers/confidentialrelay/handler.go:349-366`) only validates that `req.ID` is non-empty and ≤200 chars, then calls `h.newActiveRequest(req, callback)` (`handler.go:368-383`), which takes `h.mu.Lock()` and inserts into `h.activeRequests[req.ID]` as long as that ID is not already present. There is no per-sender or per-DON concurrency check, no authentication/authorization gate (unlike the vault handler's `requestProcessor.ProcessRequest` call before `newActiveRequest`), and no maximum size enforced on `h.activeRequests`. [1](#0-0) [2](#0-1) 

Requests reach this code path through `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`), which decodes an inbound HTTP JSON-RPC message (self-signed by an arbitrary caller-controlled key via `jsonrpc2.DecodeRequest`) and dispatches straight to `h.HandleJSONRPCUserMessage`. No node/operator credential is required — only a syntactically valid signed request with a fresh distinct `ID`, something any unprivileged network caller can produce cheaply (UUIDs). [3](#0-2) 

The only cleanup mechanism is the periodic sweep started in `Start`, which runs every `defaultCleanUpPeriod` (1 second) and removes entries older than `requestTimeout` (default 30s) via `removeExpiredRequests`, or forwards graced requests. Since expiry is time-based, not count-based, an attacker sending requests at a rate exceeding what the DON nodes can answer/expire within the sweep window causes `activeRequests` to accumulate, each entry holding a `jsonrpc.Request[json.RawMessage]`, a `responses` map, and a `Callback`. [4](#0-3) 

The vault handler (`core/services/gateway/handlers/vault/handler.go`) has the same unbounded-map pattern for `activeRequests`, but for secret methods it is gated behind `requestProcessor.ProcessRequest` (allowlist/JWT authorization) before `newActiveRequest` is called, reducing but not eliminating exposure (the `MethodPublicKeyGet` path can still create entries when the cache is stale, though this is described as aggressively cached). [5](#0-4) [6](#0-5) 

No existing check stops this: there's a `globalNodeRateLimiter`/`perNodeRateLimiters` in confidentialrelay, but these only gate `HandleNodeMessage` (responses from DON nodes), not the inbound user message path. [7](#0-6) 

### Impact Explanation
An unprivileged, unauthenticated (self-signed) caller can grow gateway-process memory unboundedly by submitting many distinct-ID confidential-relay requests faster than the 1-second cleanup tick and 30-second timeout can drain them, since there is no concurrency quota per caller or globally on `h.activeRequests`. This is a resource-exhaustion / availability impact shared across all subscribers of the DON served by this gateway handler instance, matching Chainlink's "Denial of Service" / quota-bypass bounty impact class rather than a fund-loss or key-disclosure impact.

### Likelihood Explanation
Feasibility is high: the attacker needs only network access to the gateway's public JSON-RPC endpoint and the ability to self-sign a request (no operator role, no allowlist membership, no prior relationship with any DON). Generating unique request IDs and sending requests in a tight loop is trivial and fully repeatable; the fan-out to DON nodes (`fanOutToNodes`) does not block the attacker's ability to keep enqueuing new IDs, since each `HandleJSONRPCUserMessage` call returns after dispatching sends.

### Recommendation
Add a per-sender and/or global concurrency cap on `h.activeRequests` (e.g., via a `limits.GateLimiter`/counting semaphore keyed by the request's authenticated sender or source IP) that is checked in `HandleJSONRPCUserMessage` before `newActiveRequest` is called, rejecting new requests with `api.LimitExceededError` once the cap is reached. Consider also authenticating/authorizing confidential-relay requests earlier in the flow (as the vault handler does) so unauthenticated senders cannot create server-side state at all.

### Proof of Concept
Go unit test plan (table/integration-style, mirroring existing tests in `core/services/gateway/handlers/confidentialrelay/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a mocked `gwhandlers.DON` (`SendToNode` returns `nil`) and a `clockwork.FakeClock` so the cleanup ticker does not fire during the test.
2. In a loop, submit N (e.g. 10,000) `jsonrpc.Request[json.RawMessage]` calls to `h.HandleJSONRPCUserMessage`, each with a distinct UUID `ID`, minimal valid `Method`/`Params`, and a fresh `handlerscommon.NewCallback()` (do not wait on the callback, simulating a flooding client that doesn't care about responses).
3. Assert `err == nil` for every call (no quota enforcement rejects it) and that `len(h.activeRequests) == N` after the loop, confirming unbounded growth within a single cleanup period.
4. Advance the fake clock past `requestTimeout` and trigger the cleanup tick to show entries are only removed by time-based expiry, not by any concurrency cap, and that nothing in `HandleJSONRPCUserMessage`/`newActiveRequest` prevents the map from growing arbitrarily large before that sweep.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L269-339)
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

func (h *handler) Close() error {
	return h.StopOnce("ConfidentialRelayHandler", func() error {
		h.lggr.Info("closing confidential relay handler")
		close(h.stopCh)
		var err error
		if h.globalNodeRateLimiter != nil {
			err = errors.Join(err, h.globalNodeRateLimiter.Close())
		}
		for _, rl := range h.perNodeRateLimiters {
			err = errors.Join(err, rl.Close())
		}
		return err
	})
}

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

**File:** core/services/gateway/handlers/vault/handler.go (L403-450)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	h.lggr.Debugw("handling vault request", "method", req.Method, "requestID", req.ID, "request", req)
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

	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
	if activeRequestErr != nil {
		return activeRequestErr
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
