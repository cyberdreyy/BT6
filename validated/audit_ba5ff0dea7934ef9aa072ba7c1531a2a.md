### Title
Cross-workflow request-ID collision in HTTP trigger handler allows any authorized caller to block other workflows' triggers - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The `httpTriggerHandler` deduplicates in-flight HTTP trigger requests using a single map keyed only by the client-supplied JSON-RPC `id`, shared globally across **all** workflows and callers. Because the identifier space is not scoped per-workflow (or per-owner), any caller who can obtain valid authorization for *some* workflow can squat a request ID that another, unrelated workflow's legitimate caller is about to use (or is using concurrently), causing that caller's trigger request to be rejected with a conflict error. This is directly analogous to the Nouns Builder finding: an identifier meant to uniquely scope one actor's action is derived/checked without incorporating the actor's identity, allowing an unprivileged party to grief others by "reserving" the shared ID space.

### Finding Description
`setupCallback` inserts a pending callback into `h.callbacks`, a map keyed purely by `requestID` (the client-chosen JSON-RPC `id` field), with no `workflowID`/owner component in the key: [1](#0-0) [2](#0-1) 

The check-then-insert happens after per-workflow authorization (`authorizeRequest`, which validates the caller's signature against the *target* workflow's registered keys) but the resulting slot is global: [3](#0-2) 

Because authorization is scoped to "is this signer authorized for workflow X" and not to "is this ID globally unused across the whole gateway", a caller only needs valid credentials for **their own** workflow — which they legitimately possess — to occupy any `requestID` string in the shared namespace. Once occupied, any other unrelated workflow's request using the same `id` value is rejected: [4](#0-3) 

The slot is later freed either when the attacker's own request completes (`cleanupCallback`, called from `HandleNodeTriggerResponse`) or via the periodic reaper after `MaxTriggerRequestDurationMs`/`CleanUpPeriodMs`: [5](#0-4) [6](#0-5) 

This mirrors the Nouns Builder root cause exactly: a supposedly per-actor identifier (`proposalId` there, `requestID`/`h.callbacks` key here) is checked/reserved without binding the caller's identity (proposer address there, workflowID/owner here) into the key, so any actor able to submit a validly-authorized action can occupy the shared slot and repeatedly free/re-occupy it to grief a specific victim (proposal there; a specific workflow's trigger request here).

### Impact Explanation
An attacker who legitimately controls at least one workflow (registering a workflow is a normal, low-privilege action, not equivalent to the victim's) can:
1. Predict or observe a `requestID` a victim workflow is likely to use (many clients use simple/sequential/deterministic IDs for idempotency or logging), and
2. Submit their own authorized trigger request with that same `id` just before the victim, occupying the global `h.callbacks[requestID]` slot.

The victim's subsequent request for the same `id` is rejected with `jsonrpc.ErrConflict` ("requestID: %s has already been used"), denying that specific execution attempt. Because the slot frees automatically on completion/timeout, the attacker can repeat this cycle to persistently block a targeted workflow trigger — a cross-user response/availability confusion and denial-of-service against the internet-facing gateway trigger endpoint, without needing any credentials belonging to the victim.

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or race a `requestID` that a specific victim workflow will use. This is plausible when: request IDs are sequential/deterministic per client integration, when IDs are reused for idempotent retries, or when an attacker simply races common short ID values against high-traffic workflows. It does not require breaking any cryptographic authorization, only owning a workflow of one's own — a low bar relative to the victim-specific privilege the check is supposed to enforce.

### Recommendation
Scope the in-flight-request deduplication key by workflow identity in addition to the client-supplied `id`, e.g. use a composite key such as `(workflowID, requestID)` or `(authorizedOwner, requestID)` for `h.callbacks`, `HandleNodeTriggerResponse` lookups, and the reaper, mirroring the recommended fix in the analog report (binding the proposer identity into the id). This prevents unrelated callers from colliding in the shared identifier namespace.

### Proof of Concept
1. Victim registers/owns workflow `W1` and plans to POST an HTTP trigger with JSON-RPC `id = "order-42"`.
2. Attacker, who owns an unrelated but validly-registered workflow `W2`, obtains a signed JWT valid for `W2` (per `WorkflowMetadataHandler.Authorize`) and submits a trigger request with the same `id = "order-42"` slightly before the victim: [7](#0-6) 
3. Attacker's request passes `authorizeRequest` (valid for `W2`) and successfully calls `setupCallback`, inserting `h.callbacks["order-42"]`.
4. Victim's request for `W1` with `id = "order-42"` reaches `setupCallback`, finds the key already present, and is rejected with `jsonrpc.ErrConflict`: [4](#0-3) 
5. The test suite confirms this exact collision behavior for a single ID (though it does not test the cross-workflow scenario), demonstrating the check is per-ID only: [8](#0-7) 

Note: I could not find (within index limits) any code that scopes `h.callbacks` by workflow or that binds `requestID` uniqueness to the authorized workflow/owner, so the composite-key gap described above appears to be a genuine, unaddressed design issue in this handler.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L53-60)
```go
type httpTriggerHandler struct {
	services.StateMachine
	config                  ServiceConfig
	shards                  []*shardEndpoint
	nodeAddrToShard         map[string]*shardEndpoint
	lggr                    logger.Logger
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L437-446)
```go
// cleanupCallback removes a callback and signals sendWithRetries to stop.
// Must be called while holding callbacksMu lock.
func (h *httpTriggerHandler) cleanupCallback(requestID string) {
	saved, exists := h.callbacks[requestID]
	if !exists {
		return
	}
	close(saved.doneCh)
	delete(h.callbacks, requestID)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L526-544)
```go
// reapExpiredCallbacks removes callbacks that are older than the maximum age
func (h *httpTriggerHandler) reapExpiredCallbacks(ctx context.Context) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()
	now := time.Now()
	var expiredCount int
	for reqID, callback := range h.callbacks {
		if now.Sub(callback.createdAt) > time.Duration(h.config.CleanUpPeriodMs)*time.Millisecond {
			h.metrics.IncrementRequestErrors(ctx, jsonrpc.ErrInternal, h.lggr)
			h.cleanupCallback(reqID)
			expiredCount++
		}
	}
	if expiredCount > 0 {
		h.metrics.IncrementPendingRequestsCleanUpCount(ctx, int64(expiredCount), h.lggr)
		h.lggr.Infow("Removed expired callbacks", "count", expiredCount, "remaining", len(h.callbacks))
	}
	h.metrics.RecordPendingRequestsCount(ctx, int64(len(h.callbacks)), h.lggr)
}
```

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L317-355)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```
