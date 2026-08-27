### Title
Confidential-relay gateway handler uses a global, unnamespaced request-ID keyspace, allowing unprivileged clients to block/collide other users' in-flight requests - (File: core/services/gateway/handlers/confidentialrelay/handler.go)

### Summary
This is a valid analog to the SEDA `requestId` collision bug. The SEDA issue root cause is that a request identifier is derived/accepted without binding it to the requester (no `msg.sender`, no nonce), so unrelated or malicious callers share one flat ID space and can block each other's requests. The Chainlink `confidentialrelay` gateway handler exhibits the same root cause: `HandleJSONRPCUserMessage` accepts an arbitrary, fully client-controlled `req.ID` and stores it in a single global map (`h.activeRequests`) with no binding to sender identity, DON, or any other unique attribute.

### Finding Description
`HandleJSONRPCUserMessage` only validates that the ID is non-empty and ≤200 characters, then calls `newActiveRequest`, which keys the handler's `activeRequests` map purely by the caller-supplied `req.ID`: [1](#0-0) [2](#0-1) 

This is a gateway JSON-RPC endpoint reachable by any unprivileged client of the internet-facing gateway (`gwhandlers.Handler` implementation invoked via `HandleJSONRPCUserMessage`). Unlike the sibling `vault` handler — which routes requests through `vaultcap`'s authorization/request-processing pipeline (`gateway_vault_request_processor.go`) that appears to prefix request IDs with the authorized owner via `vaulttypes.RequestIDSeparator` before being stored (confirmed by `sendSuccessResponse` and `errorResponse` stripping an owner-prefix from the ID) — the confidential-relay handler has no such namespacing step. There is no per-sender, per-session, or per-DON binding for the ID.

The existing unit test explicitly documents the collision behavior as global, not per-caller: [3](#0-2) 

Because two independent JSON-RPC clients can each choose `req.ID` freely, and the map is shared across all callers to the same handler instance/DON, one unprivileged client can pre-register (or race) a request under an ID that another legitimate client is about to use (or is already using), causing the legitimate caller's `HandleJSONRPCUserMessage` call to fail with `"request ID already exists"` and their request to be silently dropped/rejected.

### Impact Explanation
This mirrors the SEDA impact: a data/secret retrieval request submitted by a legitimate user can be blocked by an unrelated, unprivileged actor who front-runs the same request ID, or continuously "squats" on IDs to deny service to other tenants sharing the same confidential-relay DON/gateway. Because confidential relay is used to fetch/relay secrets (`MethodSecretsGet`) through the gateway to a DON, a denial-of-service on this request path can delay or block workflows that depend on timely secret retrieval, potentially leading to stale state or failed executions for other users — the same "protocols can't fetch the data in time" impact described in the SEDA report.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to either guess/predict another caller's chosen request ID (many clients may use predictable schemes such as counters, UUIDs derived from public info, or fixed strings) or race a specific known ID before the legitimate client's request lands. Because the check-and-insert in `newActiveRequest` is protected by a mutex (no TOCTOU within the handler itself), the primary practical attack is ID prediction/squatting rather than a race condition, which somewhat lowers likelihood compared to a pure race but is still realistic in multi-tenant deployments where client-chosen IDs are not required to be cryptographically random or bound to caller identity.

### Recommendation
Bind the internal tracking key to more than the raw client-supplied `req.ID`. At minimum, namespace `activeRequests` by a caller-identity attribute (e.g., authenticated sender/session key, similar to how the vault handler apparently prefixes with `authorizedOwner` + `vaulttypes.RequestIDSeparator`) so that two different unprivileged callers can never collide on the same internal key, even if they pick the same `req.ID`. Alternatively, generate/mix in a gateway-side nonce or connection-scoped identifier that is not attacker-controlled, and continue exposing only the original client ID in JSON-RPC responses (as `vault`'s handler already does via ID-stripping) for spec compliance.

### Proof of Concept
1. Two independent, unprivileged clients connect to the same gateway/DON confidential-relay endpoint.
2. Client B observes or predicts the `req.ID` that client A is about to submit (e.g., sequential/deterministic IDs, or simply races a commonly-used literal ID).
3. Client B sends a `HandleJSONRPCUserMessage` request with that same ID slightly before or concurrently with client A.
4. `newActiveRequest` inserts B's request first; when A's request arrives, `h.activeRequests[req.ID] != nil` is true, so A's call returns `"request ID already exists: " + req.ID"` and A's legitimate confidential-relay/secrets-get request is rejected — reproducing the exact "front-running/blocking due to non-unique request identifiers" pattern described in the SEDA report, as directly demonstrated by the existing `TestConfidentialRelayHandler_DuplicateRequestID` test.

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

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```
