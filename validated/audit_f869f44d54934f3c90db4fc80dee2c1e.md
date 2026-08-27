Confirmed — the `Authorize` function in `workflow_metadata_handler.go` has a clear check-then-act race: `isReplay()` (read-lock) and `recordUsage()` (write-lock) are separate, non-atomic critical sections separated by other logic (the authorizedKeys lookup). This is the same bug class as the external report: a security-critical state check is not updated atomically with its use, allowing an attacker to "replay" a request before the tracking state catches up.

### Title
JWT replay-protection cache check-then-act race allows single-use trigger JWT to be replayed concurrently - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
The Gateway's `WorkflowMetadataHandler.Authorize` function enforces single-use JWTs for HTTP trigger requests via a `jwtReplayCache`, checking `isReplay(claims.ID)` and later calling `recordUsage(claims.ID)` only after the request is authorized. These two operations are not atomic, and an unprivileged client controlling the workflow trigger request can exploit the gap between them to make the same signed JWT valid for multiple concurrent trigger executions.

### Finding Description
`Authorize` is invoked from `httpTriggerHandler.authorizeRequest` for every inbound HTTP trigger request reaching the gateway from an external, unprivileged sender [1](#0-0) . Inside `Authorize`, the replay check and the replay recording are two independent, non-atomic critical sections:

```
if h.jwtCache.isReplay(claims.ID) { ... return err }
...
h.jwtCache.recordUsage(claims.ID)
``` [2](#0-1) 

`isReplay` takes only an `RLock` on `jwtReplayCache.mu` and returns immediately, and `recordUsage` takes a separate `Lock` only at the very end of `Authorize`, after the (unlocked) `authorizedKeys` map lookup and signer-authorization check have executed [3](#0-2) . There is no per-`jti` mutual exclusion, so if the same signed JWT (same `jti`, same digest) is submitted twice concurrently — which is trivial for an external caller since HTTP requests are asynchronous and can be sent in parallel — both goroutines can pass `isReplay` returning `false` before either calls `recordUsage`, and both proceed to trigger the workflow. This mirrors the root cause pattern in the external report: a stale/unsynchronized state value used to gate a security-sensitive action is not updated atomically with its use, so replaying the same signed input can be exploited "by breaking protocol" (here, the single-use JWT invariant documented in the handler's own README: "JWT Verification: All trigger requests must include valid JWT tokens" implying single use) rather than through a legitimate second signature.

### Impact Explanation
This breaks the intended one-shot semantics of trigger JWTs. Since a single valid signed JWT can be replayed to fan out to all DON members multiple times concurrently, an external caller can trigger duplicate/unintended workflow executions using only one authorized signature, effectively bypassing the intended request-uniqueness/anti-replay guarantee that gates unauthorized job runs. Depending on downstream idempotency handling, this could result in duplicate workflow executions, resource/quota amplification, or repeated consumption of any execution-scoped resources tied to that single JWT — an unauthorized job-run analog to the original bug's "borrowing allowed even when protocol is underwater" impact class.

### Likelihood Explanation
Likelihood is high for any attacker with an already-authorized signing key (i.e., anyone entitled to submit at least one legitimate trigger for the workflow): they need only send the identical signed request twice in quick succession over HTTP, no special network position or node compromise required, and no additional cryptographic work beyond a normal single request.

### Recommendation
Make the replay check-and-record atomic under a single lock (e.g., a `CheckAndRecord(jti)` method that acquires the write lock once, checks for existence, and inserts in the same critical section, returning whether it was already present), and reject the request without recording if it was already used. This eliminates the window in which two calls with the same `jti` can both observe "not yet replayed."

### Proof of Concept
1. Obtain one valid signed trigger JWT (`jti = X`) for a workflow you are authorized to sign for.
2. Send the identical JSON-RPC trigger request (same body, same `Auth` JWT) to the gateway from two concurrent goroutines/clients at the same time.
3. Both requests call `WorkflowMetadataHandler.Authorize`; both call `isReplay(X)` before either finishes `recordUsage(X)` — with a small optimizer/scheduling delay in `authorizedKeys` lookup (`h.authorizedKeys[workflowID]`) providing more than enough of a race window.
4. Both requests pass authorization and are forwarded via `sendWithRetries`/`setupCallback` to the DON, resulting in two workflow trigger executions from a single-use JWT [4](#0-3) .

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-139)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-369)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
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
