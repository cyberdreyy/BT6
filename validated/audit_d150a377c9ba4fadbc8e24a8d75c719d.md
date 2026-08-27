### Title
JWT replay-protection check-then-act race allows the same JWT to authorize multiple concurrent workflow trigger executions - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
`WorkflowMetadataHandler.Authorize` performs its JWT replay check and its "record as used" write as two separate, independently-locked operations. An unprivileged HTTP-trigger caller can send multiple concurrent requests carrying the same signed JWT (`jti`) before the first request has recorded that `jti` as used, letting all of them pass the replay check and each independently authorize and fan out a `workflows.execute` trigger to the DON.

### Finding Description
`Authorize()` reads the JWT-replay cache and writes to it in two separate critical sections rather than atomically: [1](#0-0) 

`isReplay` takes an `RLock`, checks map membership, and releases the lock; `recordUsage` is only called at the very end of `Authorize`, after signature verification and the authorized-key lookup have succeeded: [2](#0-1) 

If two (or more) requests carrying the identical JWT arrive concurrently — trivially reproducible by an unprivileged client issuing parallel HTTP trigger calls with the same bearer token — both can call `isReplay(claims.ID)` and get `exists == false` before either calls `recordUsage(claims.ID)`. Both then pass authorization and are each forwarded through `httpTriggerHandler.HandleUserTriggerRequest` → `sendWithRetries` to the DON as independent `workflows.execute` triggers: [3](#0-2) 

This is directly analogous to the reported bug class: a state check (`isReplay`) is performed against a snapshot that is stale by the time the corresponding state update (`recordUsage`) is committed, and an attacker can exploit the window between "read" and "write" to get more benefit (extra authorized executions) than the single-use token was meant to grant — the same non-atomic check-then-act pattern as the conviction-score snapshot read separated from the state mutation in the reported finding.

### Impact Explanation
This bypasses a security control explicitly designed to enforce single-use JWTs (the code's own error message states "JWT token has already been used"), permitting a single signed authorization to trigger multiple concurrent workflow executions. This is a concrete unauthorized/duplicated job-run condition reachable purely from an unprivileged client of the internet-facing gateway HTTP trigger path, without needing any node/operator privilege.

### Likelihood Explanation
Likelihood is moderate-to-high: it requires no privileged access, only the ability to send a small number of concurrent HTTP requests with the same valid JWT before the first one's `recordUsage` call completes. Because the two operations are separated by full request processing (JWT signature/claims verification and an authorized-key map lookup) rather than being adjacent, the race window is not a single instruction but spans a meaningful portion of `Authorize`, making the race practically exploitable rather than a theoretical microsecond race.

### Recommendation
Make the replay check and usage recording atomic: acquire a single write lock (or use a `sync.Map`/`LoadOrStore`-style atomic check-and-set) that checks for existence of `jti` and inserts it in one critical section, rejecting the request if it was already present. E.g., replace `isReplay` + `recordUsage` with a single `checkAndRecord(jti) bool` method that holds `cache.mu.Lock()` for the entire check-and-insert operation, and call it once in `Authorize` immediately when the claims are verified.

### Proof of Concept
1. Obtain a validly signed HTTP-trigger JWT (`jti = X`) for an authorized workflow signer.
2. Fire N (e.g., 5) concurrent JSON-RPC `workflows.execute` requests to the gateway's HTTP trigger endpoint, all using the identical JWT with `jti = X`.
3. Each goroutine in the gateway calls `WorkflowMetadataHandler.Authorize`, which calls `isReplay(X)` before any of them has called `recordUsage(X)`.
4. More than one request passes authorization and is forwarded via `sendToShard`/`sendWithRetries` to the workflow DON as a distinct trigger execution, despite the single-use intent of the JWT — observable via duplicate `workflows.execute` node-bound messages and duplicate metrics increments (`IncrementTriggerCapabilityRequestCount`) for the same `jti`/workflow. [1](#0-0)

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-412)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-140)
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

	strippedWorkflowID := strings.TrimPrefix(workflowID, "0x")
	legacyExecutionID, err := workflows.EncodeExecutionID(strippedWorkflowID, req.ID) //nolint:staticcheck // legacy ID kept for observability comparison
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID: " + err.Error())
	}
	// Workflows shouldn't use more than one HTTP trigger. If we ever need to support multiple triggers, we'd need to pass
	// trigger index to the Gateway handler and somehow allow senders to pick. For now, we use trigger index 0.
	// Execution IDs here are used only for logging.
	executionIDWithTriggerIndex, err := workflows.GenerateExecutionIDWithTriggerIndex(strippedWorkflowID, req.ID, 0)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID with trigger index: " + err.Error())
	}
	h.lggr.Debugw("processing request",
		"legacyExecutionID", legacyExecutionID,
		"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
		"requestID", req.ID,
		"workflowID", workflowID)

	reqWithKey, err := reqWithAuthorizedKey(triggerReq, *key)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error marshaling trigger request: " + err.Error())
	}

	doneCh, err := h.setupCallback(ctx, req.ID, callback, requestStartTime, workflowID)
	if err != nil {
		return err
	}

	return h.sendWithRetries(ctx, legacyExecutionID, executionIDWithTriggerIndex, reqWithKey, workflowID, doneCh)
}
```
