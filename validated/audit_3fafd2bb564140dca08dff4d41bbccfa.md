### Title
Request-ID reuse after `sendResponseAndClearRequest` clears an `activeRequest` lets a colliding second request capture stale DON node responses belonging to the first request - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`h.activeRequests` is keyed purely by the client-supplied `req.ID` with no generation/epoch binding between a fan-out round and the `*activeRequest` object that issued it. `sendResponseAndClearRequest` deletes the map entry for `ar.req.ID` immediately after handing the payload to the callback, and `newActiveRequest` will happily accept a new request under the same ID the instant the slot is free. A late-arriving DON node response for the just-completed request is looked up in `HandleNodeMessage` solely by `resp.ID`, so once the ID is reused it is merged into the new request's bundle instead of being dropped.

### Finding Description
`req.ID` is fully attacker/client controlled: `Gateway.ProcessRequest` decodes it straight from the raw request (`core/services/gateway/gateway.go:220-231`) with only an empty/length check, and `handler.HandleJSONRPCUserMessage` enforces the same length bound (`core/services/gateway/handlers/confidentialrelay/handler.go:349-355`) but no uniqueness/format constraint beyond the in-memory map check.

`newActiveRequest` inserts under `req.ID` while holding `h.mu` [1](#0-0) . Completion in `sendResponseAndClearRequest` performs the CAS on `completed`, calls `ar.SendResponse(payload)`, and only afterwards deletes the map entry under a separate, brief lock acquisition: [2](#0-1) 

Because the delete and any subsequent `newActiveRequest` for the same ID are two independent critical sections rather than one atomic transaction spanning "complete + fully deliver + forbid reuse," nothing stops a second caller from registering `req.ID = X` the instant the old entry is removed. `HandleNodeMessage` resolves incoming node responses purely by `resp.ID` via `h.getActiveRequest(resp.ID)` and then calls `ar.addResponseForNode` on whatever `*activeRequest` currently occupies that slot: [3](#0-2) 

There is no check that the response actually corresponds to the generation/round of the currently-registered `activeRequest` (e.g., no per-round nonce/sequence embedded in the response or verified against `ar.req`). A stale response addressed to the ID's first occupant that arrives after deletion but during the window before/while a new request reuses the ID is silently folded into the new occupant's `responses` map and can influence `h.bundler.Bundle` output delivered to the new caller's `Callback`.

### Impact Explanation
This is cross-request/cross-user response confusion: node-produced data intended for one logical request (in the confidentialrelay handler methods `MethodSecretsGet` / `MethodCapabilityExec`) can be attributed to and included in the bundle of a different, attacker-controlled request under the same ID, corrupting quorum computation and potentially delivering DON-produced content that was not addressed to the second requester's session into that requester's response bundle. Given the handler serves confidential relay traffic (secrets retrieval), this is a request-impersonation / cross-user-response-confusion class issue.

### Likelihood Explanation
The precondition is that the attacker can supply (or predict/observe) the exact `req.ID` string used by another concurrent legitimate request and win a narrow race against `sendResponseAndClearRequest`'s delete-then-reinsert window — no privileged access, malicious node, or malicious DON member is required, since `req.ID` is entirely attacker-suppliable via the public gateway HTTP endpoint. The race window is small (delete happens right after `SendResponse` returns, and `newActiveRequest` re-checks the map under its own lock), which lowers reliability but does not eliminate the exploitable path; it is fully reproducible deterministically in a unit test by directly driving the handler's internal methods without any timing dependency.

### Recommendation
Bind each fan-out round to a unique internal generation/token (e.g., store a monotonically-increasing round ID or a fresh `*activeRequest` pointer identity check) and validate incoming node responses against that token rather than the raw client-supplied `req.ID` alone; alternatively, hold `h.mu` for the full "mark completed + deliver + delete" sequence and reject `newActiveRequest` for an ID that was very recently retired until an explicit tombstone/grace period expires, and have `HandleNodeMessage` no-op if the currently mapped `activeRequest`'s pointer differs from the one that originally issued the request to that node.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/confidentialrelay` package (white-box, uses unexported methods):
1. Construct a `handler` with a test clock and minimal DON config.
2. `ar1, _ := h.newActiveRequest(reqWithID("X"), callback1)`.
3. Call `h.sendResponseAndClearRequest(ctx, ar1, somePayload)` to simulate normal completion; assert `h.activeRequests["X"]` is now nil.
4. Immediately `ar2, err := h.newActiveRequest(reqWithID("X"), callback2)`; assert `err == nil` and `ar2 != ar1`.
5. Build a stale `jsonrpc.Response[json.RawMessage]{ID: "X", ...}` representing a late DON node reply from round 1, and call `h.HandleNodeMessage(ctx, staleResp, nodeAddr)`.
6. Assert the stale response was recorded on `ar2` (e.g., `ar2.copiedResponses()` contains `nodeAddr`) — proving the response for the first, already-completed logical request was captured by the second, unrelated request under the same ID, i.e., it was *not* correctly rejected/isolated from `ar2`'s `Callback`.

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
