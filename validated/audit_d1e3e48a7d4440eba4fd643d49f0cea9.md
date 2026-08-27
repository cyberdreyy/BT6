### Title
JWT replay-cache check-then-act race allows a captured JWT to be replayed concurrently before `recordUsage` marks it used - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` calls `jwtReplayCache.isReplay(claims.ID)` and, only after several more steps, calls `jwtReplayCache.recordUsage(claims.ID)`. Because these are two independent lock acquisitions with request processing (workflow lookup, signer authorization) happening in between, two concurrent `HandleUserTriggerRequest` calls carrying the identical JWT can both pass the replay check before either records the `jti`, allowing the same JWT to authorize two workflow executions.

### Finding Description
`isReplay` takes an `RLock`, checks map membership, and releases the lock; `recordUsage` is only invoked at the very end of `Authorize`, after workflow-ID lookup and signer-authorization checks, under a separate `Lock` acquisition: [1](#0-0) [2](#0-1) 

There is no single atomic "check-and-record" operation (unlike the analogous `RequestReplayGuard.CheckAndRecord` used in the vault JWT flow, which locks once for both the lookup and the insert): [3](#0-2) 

The attacker-reachable path is: `HTTPTriggerHandler.HandleUserTriggerRequest` → `authorizeRequest` → `WorkflowMetadataHandler.Authorize`: [4](#0-3) [5](#0-4) 

Neither `HandleUserTriggerRequest` nor `Authorize` hold any mutex around the whole `isReplay` → workflow lookup → authorization → `recordUsage` sequence, and `h.authorizedKeys` access inside `Authorize` is also unguarded by `h.mu`. If two goroutines call `Authorize` with the same token, both can observe `isReplay(claims.ID) == false` before either reaches `recordUsage`, so both proceed to send the trigger request to the DON, resulting in duplicate execution of the workflow from a single valid JWT.

### Impact Explanation
This breaks authentication/replay-protection soundness (unauthorized job run / duplicate workflow execution) for the HTTP trigger path, matching the "unauthorized job run" bounty impact class. An attacker who captures one legitimately issued JWT (e.g., sniffed from a shared/public HTTP trigger endpoint, or via a compromised client leaking a single token) can cause the target workflow to execute more than once using only that single captured token, bypassing the intended single-use JWT invariant.

### Likelihood Explanation
Exploitation requires only possession of a previously issued/valid JWT for the target workflow (no elevated node/admin privileges) and the ability to fire two requests at (or very near) the same time — both are within the described "unprivileged attacker" threat model (gateway/API caller with a signed request). The race window is real: `isReplay` and `recordUsage` are separated by multiple non-trivial steps (JWT verification already happened earlier, but workflow lookup and signer-authorization checks occur strictly between the check and the record), which widens the exploitable window under concurrency, especially under load or with the two requests targeting slightly staggered timing to maximize overlap.

### Recommendation
Replace the separate `isReplay`/`recordUsage` calls with a single atomic check-and-record operation performed under one lock (mirroring `RequestReplayGuard.CheckAndRecord` in `core/capabilities/vault/request_replay_guard.go`), e.g. add a `jwtReplayCache.checkAndRecord(jti) error` method that takes the write lock once, checks membership, and inserts atomically, then update `Authorize` to call it immediately after JWT verification (before or combined with authorization checks) so no window exists between check and record.

### Proof of Concept
Go test plan (table/goroutine-based), to be added to `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go`:
1. Set up a `WorkflowMetadataHandler` with a registered workflow and authorized signer key, as done in `TestWorkflowMetadataHandler_Authorize` (`t.Run("JWT replay protection", ...)`).
2. Create one signed JWT `tokenString` bound to a fixed request `req`.
3. Spawn N (e.g. 10) goroutines that all call `handler.Authorize(workflowID, tokenString, req)` concurrently, each recording `(key, err)` into a results slice, using a `sync.WaitGroup`.
4. To widen the race window deterministically, temporarily instrument/mock `jwtReplayCache` (or use `-race` with a tight loop and repeat the test many times) so that `isReplay` and `recordUsage` are separated by an injected small delay simulating the existing workflow-lookup/authorization steps.
5. Assert that exactly one goroutine gets `err == nil` and the rest get the "JWT token has already been used" error; a failing/vulnerable build will show more than one goroutine succeeding.
6. Additionally run with `go test -race` to confirm the map accesses in `authorizedKeys`/`jwtReplayCache.cache` are safe once fixed, and unsafe (or racy count > 1 success) before the fix.

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

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
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
