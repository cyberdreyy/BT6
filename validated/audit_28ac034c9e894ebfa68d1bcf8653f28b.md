### Title
Unauthenticated cache-miss flood of `MethodPublicKeyGet` triggers unbounded per-request DON fan-out and `activeRequests` map growth - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` deliberately skips `h.requestProcessor.ProcessRequest` authorization for `vaulttypes.MethodPublicKeyGet` [1](#0-0) . When `h.getCachedPublicKey()` returns `nil` (cache miss), every request — regardless of `req.ID` uniqueness and with no credential requirement — calls `h.newActiveRequest` and `h.handlePublicKeyGet`, which fans the request out to every DON member node via `h.don.SendToNode` [2](#0-1) . There is no per-caller or global rate limiter guarding this ingress path before `newActiveRequest`.

### Finding Description
The comment "Public key requests don't require authorization... we cache this value quite aggressively so don't need to worry about DoS" assumes the cache is always populated, but the cache has no TTL-based invalidation logic in code — `defaultPublicKeyGetCacheDurationSeconds` is declared but never referenced anywhere in the file/handler, confirmed via repo-wide search returning only the single declaration. The cache (`h.cachedPublicKeyGetResponse` / `h.cachedPublicKeyObject`) is populated only by `tryCachePublicKeyResponse`, invoked from `HandleNodeMessage` after a successful `handlePublicKeyGet` round trip [3](#0-2)  or by the periodic 1-minute refresh ticker in `Start` [4](#0-3) .

During any window where the cache is empty or stale-and-failing (node startup before the first successful fetch, or persistent inability to reach quorum from the DON), every incoming `MethodPublicKeyGet` request with a unique `req.ID` reaches the `cachedPublicKey == nil` branch, calls `h.newActiveRequest` (adding an entry to the in-memory `h.activeRequests` map, protected only by a mutex, with cleanup only after `requestTimeout` via `removeExpiredRequests`) [5](#0-4) , and immediately calls `h.fanOutToVaultNodes`, sending the request to every member of `h.donConfig.Members` [6](#0-5) . Tracing the call chain from the HTTP/gateway ingress (`gateway.ProcessRequest` → `handler.HandleJSONRPCUserMessage`) confirms no authentication, JWT check, or rate limiter runs before this dispatch for the `MethodPublicKeyGet` branch specifically [7](#0-6) . `nodeRateLimiter` only throttles inbound node responses in `HandleNodeMessage`, not user-triggered fan-out [8](#0-7) . Unlike the confidential-relay and HTTP-trigger handlers, which apply `userRateLimiter`/per-sender rate limiting before fan-out [9](#0-8) , the vault handler has no equivalent gate on this unauthenticated code path.

### Impact Explanation
An unauthenticated attacker who floods the gateway with unique-ID `MethodPublicKeyGet` requests during any cache-empty/cache-failure window can cause: (1) unbounded growth of the `activeRequests` map (bounded only by the time-based cleanup, so an attacker sustaining request rate faster than `requestTimeout`/`defaultCleanUpPeriod` can grow memory usage), and (2) request amplification onto every DON node member for each unique request, since `fanOutToVaultNodes` iterates `h.donConfig.Members`. This matches a resource-exhaustion / amplification-via-unauthenticated-endpoint impact class — it does not by itself leak secrets or bypass authorization for secret operations, but it can degrade gateway/DON availability with zero credentials.

### Likelihood Explanation
No credentials are needed — only knowledge of the gateway URL, matching the stated unprivileged-attacker model. The attack is fully reproducible whenever the public-key cache is empty (startup, or after quorum failures from the DON), which is not attacker-controlled but is a realistic condition (e.g., during rolling restarts, DON downtime, or transient network partitions between gateway and DON). Once the cache is warm, the same flood is inert because `handlePublicKeyGetSynchronously` serves from cache without touching `newActiveRequest`/DON fan-out, so the exploitable window is bounded to cache-miss periods, not indefinite.

### Recommendation
Add an unauthenticated-safe rate limiter (per-sender/global, similar to `userRateLimiter` used by the HTTP trigger handler) in front of the `MethodPublicKeyGet` cache-miss branch in `HandleJSONRPCUserMessage`, and/or coalesce concurrent cache-miss requests into a single in-flight DON fetch (singleflight pattern) so that N simultaneous callers during a cache-miss window produce only one `fanOutToVaultNodes` call rather than N. Additionally, either wire up `defaultPublicKeyGetCacheDurationSeconds` to actual TTL logic or remove the dead constant to avoid misleading the "aggressively cached" assumption in the code comment.

### Proof of Concept
Go handler-level test plan:
1. Build a `handler` via `newHandlerWithAuthorizer` with a mocked `DON` (`mocks.NewDON`) and no cached public key (`cachedPublicKeyGetResponse == nil`).
2. Set `don.On("SendToNode", ...)` expectation and a counter.
3. Loop N times (e.g., 1000), calling `h.HandleJSONRPCUserMessage(ctx, jsonrpc.Request{ID: uuid.New().String(), Method: vaulttypes.MethodPublicKeyGet}, common.NewCallback())` with unique IDs and no `Auth` field set.
4. Assert: all N calls succeed without error (no rate limiting/authorization error returned), `don.SendToNode` is called `N * len(donConfig.Members)` times, and `len(h.activeRequests)` equals N before any cleanup tick — demonstrating no rate limiter or dedup mechanism bounds `newActiveRequest`/fan-out calls for unauthenticated cache-miss `MethodPublicKeyGet` traffic.
5. Optionally assert current absence of any `limits.RateLimiter`/`GateLimiter` field guarding this branch by inspecting `handler` struct fields used in the `MethodPublicKeyGet` cache-miss path (only `nodeRateLimiter`, `writeMethodsEnabled`, `requestProcessor` exist, none applied here).

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L290-300)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L523-526)
```go
	switch resp.Method {
	case vaulttypes.MethodPublicKeyGet:
		h.tryCachePublicKeyResponse(resp, l)
	default:
```

**File:** core/services/gateway/handlers/vault/handler.go (L682-698)
```go
func (h *handler) handlePublicKeyGet(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
	if cachedPublicKey != nil {
		l.Debugw("returning cached public key response")
		return h.sendSuccessResponse(ctx, l, ar, &jsonrpc.Response[json.RawMessage]{
			Version: jsonrpc.JsonRpcVersion,
			ID:      ar.req.ID,
			Method:  ar.req.Method,
			Result:  (*json.RawMessage)(&publicKeyResponseBytes),
		})
	}

	l.Debugw("cache stale: forwarding request to nodes", "now", h.clock.Now())
	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L726-741)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
```

**File:** core/services/gateway/gateway.go (L264-277)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L371-396)
```go
func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
```
