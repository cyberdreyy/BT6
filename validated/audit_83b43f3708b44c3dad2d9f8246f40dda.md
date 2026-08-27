### Title
JWT replay cache has a TOCTOU race between `isReplay` check and `recordUsage` write, allowing a single valid JWT to authorize concurrent duplicate trigger executions - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `jwtCache.isReplay(claims.ID)` and only calls `jwtCache.recordUsage(claims.ID)` after several more steps (authorized-key lookup), with no lock held across the whole check-then-act sequence. Two concurrent `HandleUserTriggerRequest` calls carrying the same JWT (`jti`) can both pass the replay check before either records usage, causing the JWT to authorize more than one trigger execution.

### Finding Description
`jwtReplayCache.isReplay` takes an `RLock`, checks membership, and releases the lock [1](#0-0)  and `recordUsage` is only invoked later, in `Authorize`, after the authorized-key lookup succeeds [2](#0-1) . Because `isReplay` and `recordUsage` are two separate, independently-locked operations rather than one atomic "check-and-set", two goroutines calling `Authorize` concurrently with the same `jti` will both see `isReplay(claims.ID) == false`, both pass the authorized-key check, and both call `recordUsage` — the second call is a no-op overwrite, not a rejection. This is invoked from `httpTriggerHandler.authorizeRequest`, which is reached directly from the attacker-controlled `HandleUserTriggerRequest` entrypoint for every gateway HTTP trigger request [3](#0-2) [4](#0-3) .

Regarding the specific "different workflowID" variant: `Authorize`'s authorized-key check is scoped per `workflowID` (`h.authorizedKeys[workflowID]`), so a replayed JWT only succeeds against a second `workflowID` if the same signer key is also present in that workflow's authorized-key set [5](#0-4) . This limits true cross-*owner* impact — an attacker cannot use another user's captured JWT against a workflow they don't control unless their key is also authorized there. However, the core race itself is real and workflow-independent: the check-then-act gap between `isReplay` and `recordUsage` is not guarded by a single lock, so the replay protection can be bypassed for concurrent identical requests (same `jti`, same or different authorized workflowID), producing duplicate downstream `sendWithRetries`/trigger executions from what should be a single-use signed request.

### Impact Explanation
This breaks the request-binding/replay-prevention invariant: a single valid signed trigger request can be turned into two (or more) accepted trigger executions if fired concurrently within the race window, each producing its own `setupCallback`/`sendWithRetries` flow and consuming rate-limit/DON resources twice for one signed intent. This matches the "unauthorized job run" / duplicate execution bounty impact class, though it is not a cross-user privilege escalation since the authorized-key check still gates access per workflowID.

### Likelihood Explanation
Exploitability requires only a captured valid JWT the attacker is already authorized to use (workflow owner replaying their own token, or an attacker who intercepted/observed one signed request) and the ability to fire two requests to the gateway with negligible time skew — no elevated privileges are needed beyond what is required to legitimately submit one signed request. The race window is small (between the `RUnlock` in `isReplay` and the later `Lock` in `recordUsage`, which also requires passing the authorized-key map lookup in between) but is deterministically reproducible under concurrent load or with lock-step goroutines in tests.

### Recommendation
Merge `isReplay` and `recordUsage` into a single atomic check-and-set operation performed while holding the cache's write lock once per `Authorize` call, e.g. a `checkAndRecord(jti) (alreadyUsed bool)` method that does the lookup and insertion under one `Lock()`, and reject the request if the entry already existed.

### Proof of Concept
Go unit test in `workflow_metadata_handler_test.go`:
1. Construct two `jsonrpc.Request` objects with identical `Auth` JWT (same `jti`) but different `workflowID`s, both present in `authorizedKeys` for the same signer.
2. Use a synchronization point (e.g., a custom `jwtReplayCache` wrapper or `sync.WaitGroup` + tight timing) to invoke `WorkflowMetadataHandler.Authorize` from two goroutines simultaneously with the same `jti`.
3. Assert that only one of the two concurrent calls to `Authorize` succeeds (returns a non-nil key) and the other returns the "already been used" error; currently both can succeed, demonstrating the race.
4. Extend the test to directly call `jwtCache.isReplay` then `jwtCache.recordUsage` from two goroutines with a controlled interleaving (unlock isReplay, delay, then recordUsage) to deterministically show both return `false` for the replay check.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-405)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-102)
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
