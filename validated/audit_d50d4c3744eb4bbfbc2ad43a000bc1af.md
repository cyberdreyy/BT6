### Title
Request-ID reuse race lets a late node response for a just-completed request be attributed to a different caller's new request with the same ID - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`h.activeRequests` is keyed solely by the client-supplied `req.ID` string, with no per-instance binding token. `sendResponseAndClearRequest` deletes the map entry as soon as a request completes, but slower nodes may still be "in flight" for that same completed request. If a new request reuses the freed ID before the stale node response arrives, `HandleNodeMessage` will route that stale, previously-in-flight response into the new (unrelated) caller's `activeRequest`, mixing another party's signed relay result into a different caller's bundle.

### Finding Description
`newActiveRequest` only guards against a currently-registered duplicate ID: [1](#0-0) 

`sendResponseAndClearRequest` frees the ID from `h.activeRequests` as soon as the first completion path wins the `CompareAndSwap`, independent of whether all fanned-out nodes have replied yet: [2](#0-1) 

`fanOutToNodes` sends the request to every DON member concurrently and returns once the sends complete, but node *responses* keep arriving asynchronously via `HandleNodeMessage` long after the gateway may have already reached quorum and answered the caller (e.g., via the 2F+1 early-forward path or quorum-grace path): [3](#0-2) 

`HandleNodeMessage` looks the target request up purely by `resp.ID` with no additional binding (no per-instance nonce/generation counter), and unconditionally attaches any matched response via `addResponseForNode`: [4](#0-3) 

Exploit flow:
1. Attacker (or two independent gateway clients whose SDKs pick low-entropy/incrementing JSON-RPC IDs) has request A in flight with `req.ID = "X"`, fanned out to N nodes.
2. Request A reaches quorum on 2F+1 nodes; `forwardBundle` → `sendResponseAndClearRequest` answers the caller and deletes `h.activeRequests["X"]` while some slower nodes' responses for A are still traveling.
3. Immediately after the delete, a new `HandleJSONRPCUserMessage` call (from a different caller, or from any client) with the same `req.ID = "X"` succeeds in `newActiveRequest`, since the map slot is now empty, creating request B.
4. The stale, still-in-flight node response for A arrives and is matched to B by `getActiveRequest("X")`, then merged into B's `responses` via `addResponseForNode`.
5. When B's bundle is built and forwarded (`bundler.Bundle`), it can include this signed response object that was actually produced for A's method/params/owner, i.e., data belonging to a different request/caller ends up inside B's response bundle returned to B's caller.

Existing checks (empty-ID rejection, length limit, per-node/global rate limiting, in-map duplicate rejection) do not close this window because none of them bind a node response to a specific `activeRequest` instance beyond the reusable string ID.

### Impact Explanation
This is a cross-user response confusion / confidential data leak: a signed capability-execution or secrets-get result intended for one request/owner can be folded into a different, unrelated request's response bundle and returned to a different caller. Given the handler's own stated design ("gateway is a dumb fan-in... forwards every per-node signed response it collected"), any bundled entry is treated by the enclave as a candidate signed response, so contamination is not merely cosmetic — it changes what data is delivered to the wrong caller.

### Likelihood Explanation
Exploitability requires: (a) knowledge of, or collision with, another in-flight request's exact `req.ID` (plausible with predictable/incrementing client-assigned IDs, which is a common JSON-RPC client pattern), and (b) winning a short race window between `sendResponseAndClearRequest`'s delete and a still-in-flight late `HandleNodeMessage` call for the old ID. This window is narrow but real under normal network jitter/slow nodes, and requires only gateway-client credentials (no elevated privilege), matching the specified unprivileged threat model.

### Recommendation
Bind node responses to a specific `activeRequest` instance rather than the reusable string ID alone — e.g., generate a unique internal token/generation counter per `newActiveRequest` call, include/expect it in `SendToNode`/`HandleNodeMessage` correlation (or store it alongside the map entry and validate `ar` identity before merging), and/or delay ID reuse until all outstanding per-node sends for that ID have definitively timed out or a monotonically increasing epoch guards against stale writes to a newer instance sharing the same ID.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Call `h.HandleJSONRPCUserMessage` with `req.ID = "X"` (ar1) using a DON where `F=1`/4 members; mock `SendToNode` to succeed for all nodes.
2. Deliver 3 signed `HandleNodeMessage` calls for 3 of the 4 nodes to reach 2F+1 and trigger `forwardBundle` → `sendResponseAndClearRequest`; assert `h.getActiveRequest("X")` is now `nil`.
3. Immediately call `h.HandleJSONRPCUserMessage` again with the same `req.ID = "X"` and a new callback `cb2` (ar2); assert it succeeds (no "request ID already exists" error).
4. Deliver the 4th node's (originally destined for ar1) `HandleNodeMessage` for ID "X" with a payload/signature that is clearly attributable to ar1 (e.g., a distinct workflow/payload marker).
5. Drive ar2 to completion (feed remaining signed responses for ar2's own nodes) and inspect the bundle delivered to `cb2`.
6. Assert that the bundle delivered to `cb2` does **not** contain the response payload/marker that belongs to ar1 — currently this assertion fails because `addResponseForNode` merges the late ar1 response into ar2's `responses` map, and the resulting bundle can include ar1's signed result in ar2's caller's response.

### Citations

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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L391-418)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L654-679)
```go
// sendResponseAndClearRequest claims the request, sends payload, and removes it from
// activeRequests. Concurrent completion paths (node-message forward,
// terminal-state forward, quorum grace, expiry) may all race here; only the first
// claimer sends. Metrics are recorded only after a successful send so losers do not
// double-count.
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
