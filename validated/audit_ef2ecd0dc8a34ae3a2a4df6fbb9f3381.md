### Title
Unauthenticated flood of confidential-relay requests exhausts the shared `globalNodeRateLimiter`, causing silent drop of legitimate victim node responses - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` accepts any incoming relay request (only an ID length check is performed) and immediately fans it out to every DON member via `fanOutToNodes`, with no per-caller authentication or rate limiting at the gateway layer. Every node response to any request — including error responses to unauthorized/garbage requests — passes through `HandleNodeMessage`, which gates on `h.globalNodeRateLimiter.Allow(ctx)` before looking up the target `activeRequest`, so one caller flooding requests can exhaust the single shared global budget and cause an unrelated victim's legitimate node responses to be silently dropped.

### Finding Description
`HandleJSONRPCUserMessage` [1](#0-0)  only validates `req.ID` presence/length and creates an `activeRequest`, then calls `fanOutToNodes`, which sends the request to every DON member [2](#0-1) . There is no per-caller authorization or per-caller rate limit applied at this intake point (contrast with `vault/handler.go`'s `HandleJSONRPCUserMessage`, which calls `h.requestProcessor.ProcessRequest` before creating an active request [3](#0-2) ).

Each DON node that receives the request — whether it ultimately authorizes it or rejects it (e.g., failed attestation/workflow authorization in `core/capabilities/confidentialrelay/handler.go`) — replies with a JSON-RPC response that flows back into the gateway's `HandleNodeMessage`: [4](#0-3) 
This function checks `perNodeRateLimiters[nodeAddr]` first, then `h.globalNodeRateLimiter.Allow(ctx)` — both shared token buckets, the global one across *all* DON members and *all* active requests for the handler instance. On failure it logs at debug level and returns `nil`, i.e., the response is silently dropped with no error surfaced to the caller waiting on that `activeRequest`.

Because `fanOutToNodes` sends to every member for every accepted request, and the global limiter is consumed per received node response regardless of which caller originated the underlying request, an attacker who can submit a high volume of concurrent `HandleJSONRPCUserMessage` calls (e.g., via a workflow/job that is technically unauthorized at the node but still reaches the gateway relay method, or via any credential able to reach the gateway's relay JSON-RPC method) can generate enough node response traffic to exhaust `globalNodeRateLimiter`'s budget. While that budget is exhausted, a victim's in-flight request's node responses arriving at the same time are dropped via the same `l.Debug("global relay rate limit exceeded"); return nil` path, with no compensating per-caller/per-request isolation.

### Impact Explanation
This is a Denial-of-Service against the confidential-relay gateway path: a resource shared across the whole DON and all active requests can be starved by a single caller's traffic, causing legitimate victim node responses to be dropped silently (no timeout/error signal until the victim's own `RequestTimeoutSec` elapses). This falls under an availability/quota-isolation weakness — one caller's requests are not isolated from another's, defeating the purpose of having both a per-node and a "global" limiter as DoS protection.

### Likelihood Explanation
Preconditions: the attacker needs the ability to submit `HandleJSONRPCUserMessage` calls to the confidential-relay gateway handler (i.e., reach the gateway's relay JSON-RPC method with a syntactically valid request/ID); there is no additional authorization gate at this intake function before fan-out, so the bar is low compared to genuinely getting the request accepted by nodes — the node-side rejection still produces a rate-limiter-consuming response. This is repeatable and does not require any privileged role, only the ability to send enough concurrent malformed/rejected relay requests to the gateway.

### Recommendation
Add per-caller (or per-request/per-source) admission control before fan-out in `HandleJSONRPCUserMessage`, independent of the shared node-response rate limiters, and/or make `globalNodeRateLimiter` fairness-aware (e.g., partition by request ID / requester so one flood cannot starve unrelated requests). Additionally, surface a distinct error/backpressure signal to the caller when `HandleJSONRPCUserMessage` itself is being throttled, rather than only throttling at the node-response ingestion side.

### Proof of Concept
Extend `TestConfidentialRelayHandler_RateLimitedNode`-style tests in `handler_test.go`:
1. Configure `h.globalNodeRateLimiter = limits.GlobalRateLimiter(low_rate, small_burst)` and a generous `perNodeRateLimiters`.
2. Concurrently call `h.HandleJSONRPCUserMessage` many times with distinct request IDs simulating an attacker's flood (or directly simulate many `HandleNodeMessage` calls from the DON responding to that flood) to exhaust the global budget.
3. In parallel, call `h.HandleJSONRPCUserMessage` for a "victim" request ID, then deliver a legitimate `HandleNodeMessage` response for the victim's ID while the global limiter is exhausted.
4. Assert `HandleNodeMessage` for the victim returns `nil` (no error) and the victim's callback (`cb.Wait`) times out / never receives a response, demonstrating silent drop with no per-caller isolation.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L431-446)
```go
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
```
