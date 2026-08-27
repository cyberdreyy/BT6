### Title
JWT Replay-Protection Cache in Gateway HTTP Trigger Handler Has a Check-Then-Act Race, Allowing Multiple Workflow Executions From a Single Signed Request - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
The Gateway's `WorkflowMetadataHandler.Authorize` function performs JWT replay protection by checking a cache with `isReplay(claims.ID)` and, only after the authorization checks succeed, marking the JWT as used with `recordUsage(claims.ID)`. These are two separate, non-atomic operations on the shared `jwtReplayCache`, creating a check-then-act (TOCTOU) race: concurrent requests carrying the same signed JWT (same `jti`) can both pass the "not yet used" check before either records its usage, resulting in a single client-signed trigger request being accepted and forwarded to the DON more than once.

### Finding Description
`Authorize` verifies the JWT signature/claims, then explicitly relies on the `jwtReplayCache` to enforce single-use semantics for a given `jti`: [1](#0-0) 

The sequence is:
1. `h.jwtCache.isReplay(claims.ID)` — read/check whether the token has been used.
2. Lookup `authorizedKeys[workflowID]` and validate the signer is authorized.
3. `h.jwtCache.recordUsage(claims.ID)` — mark the token as used, only at the very end.

Because the "used" state is only written after all the intervening authorization work completes, and the check (`isReplay`) and the write (`recordUsage`) are not combined into a single atomic operation (e.g., a compare-and-swap or a lock held across both steps), two (or more) goroutines invoked concurrently for the same JWT can each observe `isReplay == false` before either calls `recordUsage`. Both requests then pass authorization and are treated as legitimately authorized to trigger the workflow.

This is directly analogous to the Zebra CVE's root cause: a caching optimization intended to short-circuit expensive re-verification does not account for the fact that the state it is caching (single-use / not-yet-consumed) can change concurrently, and the check is trusted without being made atomic with the state mutation — leading the system to accept something (a block / a JWT-authorized request) that violates the invariant the cache was supposed to enforce (validity-at-height / one-time-use).

`Authorize` is reachable from the internet-facing Gateway path: an unprivileged client submits an HTTP trigger request with a JWT to the `httpTriggerHandler`, which resolves the workflow and calls into `WorkflowMetadataHandler.Authorize` before fanning the request out to DON nodes, as shown by the JWT-authorization test harness: [2](#0-1) 

### Impact Explanation
A single valid, signed JWT-authorized trigger request (which is meant to be single-use, per the `jti` replay-cache design) can be replayed/duplicated by an unprivileged requester (or via network retry/replay by any party who captured the token) to cause the workflow to be executed more than once on the DON. For workflows that perform on-chain actions or move funds based on the trigger, this can result in unauthorized duplicate job/workflow execution — effectively a request-impersonation/duplication bypass of the "authorize once" guarantee that the replay cache exists to enforce.

### Likelihood Explanation
Exploitation only requires the ability to send the same signed JSON-RPC request to the Gateway twice in quick succession (well within the race window between the `isReplay` check and `recordUsage`), which is trivial for any client already holding a valid JWT for a request (the legitimate requester itself, or anyone who intercepts/replays the request before the first authorization completes). No privileged access or malicious node/peer behavior is required — it is purely a client-facing race in an unprivileged, internet-reachable code path.

### Recommendation
Make replay-check-and-record atomic: acquire the `jwtReplayCache` write lock once and, within that single critical section, check for existing usage and record the new usage (or use an atomic `LoadOrStore`-style primitive) before proceeding with authorization decisions. Alternatively, record the `jti` as "reserved" immediately upon receipt (before performing the authorized-key lookup) and roll back / evict the cache entry if the request is subsequently rejected for the workflow-lookup steps.

### Proof of Concept
Conceptual PoC (cannot execute in this environment, but the race is directly derivable from the code):
1. Mint a single valid JWT for a `MethodWorkflowExecute` trigger request bound to an authorized signer, per `TestHttpTriggerHandler_HandleUserTriggerRequest_JWTAuthorization`.
2. Fire two goroutines simultaneously calling `WorkflowMetadataHandler.Authorize(workflowID, token, req)` with the identical token/`jti`.
3. Because `isReplay` and `recordUsage` are separate lock acquisitions with authorized-key lookup work interleaved between them, both goroutines can observe `isReplay(claims.ID) == false` and both return a valid `*AuthorizedKey`, causing the caller (`httpTriggerHandler`) to dispatch the workflow-execute request to the DON twice from a single signed client request.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-108)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1028-1054)
```go
	t.Run("successful JWT authorization", func(t *testing.T) {
		callback := hc.NewCallback()

		triggerReq := createTestTriggerRequest(workflowID)
		reqBytes, err2 := json.Marshal(triggerReq)
		require.NoError(t, err2)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}

		jwtToken := createTestJWTToken(t, req, privateKey)
		req.Auth = jwtToken

		mockDon.EXPECT().SendToNode(mock.Anything, "node1", mock.MatchedBy(func(r *jsonrpc.Request[json.RawMessage]) bool {
			var params gateway_common.HTTPTriggerRequest
			err = json.Unmarshal(*r.Params, &params)
			return err == nil && params.Key.PublicKey == key.PublicKey
		})).Return(nil)
		mockDon.EXPECT().SendToNode(mock.Anything, "node2", mock.Anything).Return(nil)
		mockDon.EXPECT().SendToNode(mock.Anything, "node3", mock.Anything).Return(nil)

		err = handler.HandleUserTriggerRequest(ctx, req, callback, time.Now())
```
