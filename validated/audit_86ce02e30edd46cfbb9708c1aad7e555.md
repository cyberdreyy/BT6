### Title
Per-node shared rate limiter in `HandleNodeMessage` lets an authorized-but-unprivileged sender starve other users' vault responses from the same node - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`h.nodeRateLimiter.Allow(nodeAddr)` in `HandleNodeMessage` uses a single rate-limit bucket keyed only by `nodeAddr`, shared across **all** in-flight requests handled by that node, not per-request or per-requester. Any authorized (even minimally privileged) caller who can repeatedly trigger legitimate vault requests (e.g. `MethodSecretsList`, `MethodSecretsCreate`, or a stale `MethodPublicKeyGet`) causes each DON node to emit a response back through the gateway, and enough of these exhaust the shared `PerSenderBurst`/`PerSenderRPS` allotment for that `nodeAddr`, causing subsequent legitimate responses (including a victim's) from the same node to be silently dropped at `l.Debugw("node is rate limited"...); return nil`.

### Finding Description
`HandleNodeMessage` (core/services/gateway/handlers/vault/handler.go:489-496) checks `h.nodeRateLimiter.Allow(nodeAddr)` before doing anything with the response, including looking up the `activeRequest` by `resp.ID`. This check is keyed purely by `nodeAddr` and is **not** scoped per request/requestID/owner. Every legitimate vault request that reaches a node (`fanOutToVaultNodes`, called from `handleSecretsCreate`/`Update`/`Delete`/`List`/`handlePublicKeyGet`) results in a node round-trip whose response consumes one token from that shared bucket when it arrives at `HandleNodeMessage`.

An attacker only needs valid-but-limited credentials to submit vault requests that pass `h.requestProcessor.ProcessRequest` (allowlist- or JWT-based auth restricted to their own owner scope) — e.g. `MethodSecretsList` calls, which don't require write-method gating. By submitting a burst of such requests concurrently, the attacker causes many concurrent node responses to arrive for the same `nodeAddr` in a short window, exhausting `PerSenderBurst` for that node in the shared limiter. Any other in-flight request whose response also arrives from that node during the exhausted window (including a victim's unrelated, legitimate request) is dropped via `return nil` before the code ever reaches `ar.addResponseForNode` or `h.aggregator.Aggregate`. No error is returned to `HandleNodeMessage`'s caller (`connectionmanager.go` readLoop logs nothing since `err == nil`), and the victim's callback never resolves for that node's contribution — degrading/reducing quorum count, and if repeated across enough nodes, can push the victim's request into `errInsufficientResponsesForQuorum`/timeout territory (`removeExpiredRequests`) or in the worst case for near-quorum DONs, prevent it from resolving at all.

This exact per-node-shared-bucket design is also present in the confidential-relay handler and is explicitly demonstrated as intended/expected behavior by `TestConfidentialRelayHandler_RateLimitedNode` (core/services/gateway/handlers/confidentialrelay/handler_test.go:787-869), which asserts a second, unrelated request's response is silently dropped once the shared per-node bucket is exhausted by an earlier request from the same node. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
This is a cross-user denial-of-service on response delivery within a shared DON/node: an unprivileged-but-authorized user (holder of a restricted allowlist entry or JWT scoped only to their own secrets) can degrade or deny quorum/response delivery for a victim's unrelated, legitimate vault request handled by the same gateway/DON node, without needing to compromise the victim, forge signatures, or reach node message endpoints directly. This maps to a "Denial of Service — degradation of service for other users" class rather than fund loss or key disclosure; it does not by itself leak secrets or bypass authorization, but it can cause legitimate operations (secret create/update/delete/list, or a would-be attestation) to silently fail or time out for other tenants of the same DON.

### Likelihood Explanation
Feasibility is moderate: the attacker needs valid credentials sufficient to pass `requestProcessor.ProcessRequest` (allowlist membership or a JWT), which is a low bar compared to node/operator compromise, and rules explicitly permit "restricted API token or external-initiator credential holder" and "any address sending signed gateway requests" as valid attacker classes. The attacker must also time their flood so that their induced node responses coincide with the victim's in-flight request window (bounded by `requestTimeout`, default 30s) and target the same node(s) as the victim — feasible since DON membership/topology is knowable and requests fan out to all `donConfig.Members`. The `PerSenderBurst`/`PerSenderRPS` values are operator-configured; smaller bursts make this easier, but the shared-key design itself is the flaw regardless of configured values.

### Recommendation
Scope the node-response rate limiter to something that cannot be cheaply exhausted by unrelated traffic, e.g. combine `nodeAddr` with `resp.ID`/request context, or apply per-request quota tracking (one token consumed per unique active request/node pair) rather than a single shared token bucket per node across all concurrent requests. Alternatively, rate-limit at ingress per (user, node) instead of purely per node, and/or increase burst proportional to concurrently active requests so legitimate concurrent traffic cannot be starved by a single authorized-but-malicious sender's self-induced load.

### Proof of Concept
Go test plan in `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` with a single-node `donConfig`, `NodeRateLimiter` configured with a small `PerSenderBurst`/`PerSenderRPS` (e.g. burst=2), and a mocked `don.SendToNode` that succeeds.
2. Submit N (> burst) legitimate `HandleJSONRPCUserMessage` requests (e.g. `MethodSecretsList`) from an authorized owner via `newActiveRequest`/`fanOutToVaultNodes`, each with a distinct `req.ID`.
3. For each request, call `handler.HandleNodeMessage(ctx, resp, nodeAddr)` with a valid response matching each `req.ID`, driving the shared `nodeRateLimiter` bucket to exhaustion (assert some of these calls hit the `"node is rate limited"` branch and return `nil` for requests other than the first `burst` ones).
4. Submit one additional "victim" request (`req.ID = "victim-req"`) with its own callback, and call `HandleNodeMessage` with the corresponding node response while the bucket is still exhausted.
5. Assert: `HandleNodeMessage` returns `nil` (no error surfaced anywhere), the victim's callback (`cb.Wait(ctx)` with a short timeout) never resolves/times out, and `ar.addResponseForNode`/`h.aggregator.Aggregate` were never reached for the victim's response — demonstrating silent drop of a legitimate, unrelated user's response caused entirely by another sender's flood.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
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
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L832-869)
```go
	// First response from node uses the burst allowance
	err = h.HandleNodeMessage(t.Context(), &response, nodeOne.Address)
	require.NoError(t, err)

	// Verify callback was called
	ctx, cancel := context.WithTimeout(t.Context(), 100*time.Millisecond)
	defer cancel()
	resp, err := cb.Wait(ctx)
	require.NoError(t, err)
	assert.Equal(t, api.NoError, resp.ErrorCode)

	// Start a new request
	cb2 := common.NewCallback()
	req2 := jsonrpc.Request[json.RawMessage]{
		ID:     "req-ratelimit-2",
		Method: MethodCapabilityExec,
		Params: &params,
	}
	err = h.HandleJSONRPCUserMessage(t.Context(), req2, cb2)
	require.NoError(t, err)

	response2 := jsonrpc.Response[json.RawMessage]{
		Version: jsonrpc.JsonRpcVersion,
		ID:      "req-ratelimit-2",
		Method:  MethodCapabilityExec,
		Result:  &resultData,
	}

	// Second response should be rate limited (silently dropped)
	err = h.HandleNodeMessage(t.Context(), &response2, nodeOne.Address)
	require.NoError(t, err)

	// Callback should NOT be called - verify with timeout
	ctx2, cancel2 := context.WithTimeout(t.Context(), 50*time.Millisecond)
	defer cancel2()
	_, err = cb2.Wait(ctx2)
	require.Error(t, err) // Should timeout
}
```
