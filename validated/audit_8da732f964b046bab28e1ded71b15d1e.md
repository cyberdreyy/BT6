### Title
Client-supplied JSON-RPC request ID reuse allows a late/straggler node response for a completed relay request to be delivered into a different caller's newly-created request with the same ID - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`sendResponseAndClearRequest` invokes the user `Callback` (`ar.SendResponse(payload)`) before removing the `activeRequest` entry from `h.activeRequests` (the `delete` happens only after `SendResponse` returns), and separately, request IDs are attacker/client-supplied strings rather than server-generated nonces. Because `HandleNodeMessage` resolves the target `activeRequest` purely by `resp.ID` with no binding to the specific request instance or its content, a straggling node response for a just-completed request can be merged into a brand-new `activeRequest` that reused the same ID for a different caller.

### Finding Description
`HandleJSONRPCUserMessage` accepts `req.ID` from the caller with only length/non-empty checks [1](#0-0) , and `newActiveRequest` only rejects an ID if it is *currently* present in `h.activeRequests`: [2](#0-1) .

`sendResponseAndClearRequest` claims the request via an atomic CAS on `ar.completed`, then calls the user callback (`ar.SendResponse(payload)`), and only afterward takes `h.mu.Lock()` to `delete(h.activeRequests, ar.req.ID)`: [3](#0-2) .

`HandleNodeMessage` looks up the active request purely by `resp.ID` via `h.getActiveRequest(resp.ID)`, and `addResponseForNode` adds the response to whatever `activeRequest` object is currently mapped to that ID key, without validating that the response corresponds to the same request instance/content that was fanned out: [4](#0-3) [5](#0-4) .

Exploit flow:
1. User A submits a request with ID `"X"`; the gateway fans it out to DON nodes via `fanOutToNodes` [6](#0-5) .
2. The request for `"X"` completes (quorum reached, or error, or expiry) and `sendResponseAndClearRequest` sends the callback to User A. Between the `SendResponse` call and the subsequent `delete(h.activeRequests, "X")`, `"X"` is technically still present in the map, but once the delete executes, `"X"` becomes free to reuse.
3. User B immediately submits a new request that happens to reuse ID `"X"` (client-controlled, not server-generated) — `newActiveRequest` allows this since the old entry is gone.
4. A slow/straggling DON node response for the original request (User A's request) that was in flight before completion — or an out-of-order/duplicate response arriving late from a slow websocket/network path — arrives with `resp.ID == "X"`. `HandleNodeMessage` resolves `h.getActiveRequest("X")`, which now returns User B's newly-created `activeRequest`, and the stale response gets added to `addResponseForNode`, `Bundle`d, and can be counted toward User B's quorum/bundle content.

This is possible because the code has no per-instance nonce (e.g., a monotonically-increasing internal sequence number, or an ID that is invalidated for a cooldown window) separate from the client-supplied string ID, and `HandleNodeMessage` performs no correlation check beyond the map-key match.

### Impact Explanation
A stale node response tied to a different, unrelated relay session (potentially containing different confidential-relay payload/secrets material, encrypted for a different actions/exec context) can be injected into another user's request bundle, corrupting or cross-contaminating the bundle used to compute the final relayed response returned to User B. This is a cross-user response/data-confusion issue in a confidential-relay path, matching the "cross-user response confusion" impact class named in the audit scope. It does not directly grant secret disclosure or fund movement by itself, but it breaks the isolation invariant between concurrent unrelated relay requests.

### Likelihood Explanation
Exploitability requires: (a) precise timing to reuse a client-chosen ID string immediately after a prior request with that same ID completes, and (b) an actual straggling/delayed node response for the old request arriving after the ID has been reused — which depends on legitimate DON node latency/timing rather than attacker control over the DON. The attacker (an unprivileged gateway client) fully controls (a) since request IDs are client-supplied and short reuse windows are easy to construct by design (submit-then-immediately-resubmit). Condition (b), however, depends on natural network jitter or a slow node's response arriving late — this is plausible but not fully attacker-controlled, making exploitation feasible but somewhat probabilistic/timing-dependent rather than deterministic.

### Recommendation
Bind node responses to the specific request instance rather than solely to the string ID: e.g., store/compare an internal monotonic nonce or the `activeRequest` pointer captured at fan-out time inside each `SendToNode` correlation, or have `HandleNodeMessage` validate that the response matches the exact `activeRequest` object dispatched for that fan-out generation. Alternatively, immediately mark request IDs as "in cooldown" for a short interval after completion (reject reuse for e.g. a few seconds) so a straggling response cannot land on a freshly created request with the same ID, and reorder `sendResponseAndClearRequest` to delete the map entry (or mark it terminally unavailable) before invoking `SendResponse`.

### Proof of Concept
Go unit test plan (using `clockwork.NewFakeClock()` already used by the handler):
1. Construct `handler` with a fake DON and fake clock.
2. Issue request ID `"X"` from callback A, fan out to N nodes, deliver F+1 signed responses so `forwardBundleOrTerminateIfReady` calls `sendResponseAndClearRequest`, and hook a synchronization point (e.g. wrap `ar.SendResponse`) to pause after callback invocation but before `delete(h.activeRequests, ...)`.
3. On a separate goroutine, immediately after `SendResponse` for A returns (before delete executes), call `HandleJSONRPCUserMessage` again with the same ID `"X"` and callback B; assert it fails with "request ID already exists" while the old entry is still present (validating the pre-delete window is safe).
4. Release the delete, then immediately call `HandleJSONRPCUserMessage` with ID `"X"` and callback B (simulating instant ID reuse after cleanup).
5. Deliver a straggling `HandleNodeMessage` response tagged with the original request's node/signature data and `resp.ID == "X"`.
6. Assert whether the stale response is merged into User B's `activeRequest.responses` map (via bundler output/callback B payload) — a positive result (User A's stale payload data appears in User B's completed bundle/callback) confirms the cross-delivery vulnerability described.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L97-107)
```go
func (ar *activeRequest) addResponseForNode(nodeAddr string, resp *jsonrpc.Response[json.RawMessage]) bool {
	ar.mu.Lock()
	defer ar.mu.Unlock()
	_, exists := ar.responses[nodeAddr]
	if exists {
		return false
	}

	ar.responses[nodeAddr] = resp
	return true
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-365)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L391-429)
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

	ar := h.getActiveRequest(resp.ID)
	if ar == nil {
		l.Debugw("no pending request found for ID")
		return nil
	}

	added := ar.addResponseForNode(nodeAddr, resp)
	if !added {
		l.Errorw("duplicate response from node, ignoring", "nodeAddr", nodeAddr)
		return nil
	}

	copiedResponses := ar.copiedResponses()
	// Bundle readiness counts decodable signed responses, while the enclave remains
	// responsible for signature and quorum verification. A collected response cannot
	// be replaced by a later response from the same node.
	summary, err := h.bundler.Bundle(ar.req, copiedResponses, l)
	if err != nil {
		l.Errorw("failed to build relay response bundle", "error", err)
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, err))
	}
	return h.forwardBundleOrTerminateIfReady(ctx, l, ar, summary, len(h.donConfig.Members)-summary.Total(), false)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L659-679)
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

	if sendErr != nil {
		h.lggr.Errorw("error sending response to user", "requestID", ar.req.ID, "error", sendErr)
		return sendErr
	}

	h.recordMetrics(ctx, payload.ErrorCode)
	h.lggr.Debugw("response sent to user", "requestID", ar.req.ID, "errorCode", payload.ErrorCode)
	return nil
}
```
