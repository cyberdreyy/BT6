I have enough evidence to write up the analog finding now.

### Title
JWT replay protection in `WorkflowMetadataHandler.Authorize` is check-then-act (non-atomic), allowing concurrent replay of a single-use JWT to bypass authorization dedup - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
The external report's root cause is a Checks-Effects-Interactions violation: a state variable that enforces a security invariant (`tokensMintedPerAddress`) is updated *after* an operation that can be re-entered, letting the check be bypassed repeatedly. The chainlink gateway's HTTP Trigger v2 path has the same class of bug in the JWT single-use ("replay") check used to authorize unprivileged, internet-facing trigger requests: the "check" (`isReplay`) and the "effect" (`recordUsage`) are two separate, independently-locked operations instead of one atomic check-and-set, creating a race window in which the same signed JWT can be accepted multiple times concurrently.

### Finding Description
`WorkflowMetadataHandler.Authorize` is invoked by `httpTriggerHandler.authorizeRequest` for every inbound, unauthenticated-by-session HTTP trigger request reaching the gateway (an unprivileged, internet-facing endpoint): [1](#0-0) 

Inside `Authorize`, the JWT replay guard is implemented as two separate mutex-protected calls rather than one atomic check-and-record operation: [2](#0-1) 

`isReplay` takes an `RLock`, checks membership, and releases the lock; `recordUsage` is only called later, after signer/authorization checks succeed, and takes its own `Lock` to write the entry: [3](#0-2) 

Because the "effect" (marking the `jti` as used) happens only after the full authorization flow completes, and is not combined atomically with the "check", two (or more) concurrent requests carrying the identical signed JWT can both pass `isReplay` before either calls `recordUsage`. This is structurally identical to the reported bug class: a security-relevant state mutation (`tokensMintedPerAddress++` / here, `jwtCache.recordUsage`) is deferred until after other logic runs, so the check that is supposed to gate on that state can be satisfied by concurrently in-flight requests that haven't yet been recorded — the same "check happens on stale state because effects are delayed" root cause as the CEI violation in `NextGenCore::mint`.

By contrast, the codebase demonstrates awareness of this exact class of issue and the correct fix elsewhere: `core/capabilities/vault/request_replay_guard.go`'s `CheckAndRecord` performs the check and the write under a single held lock, making it atomic: [4](#0-3) 

The HTTP Trigger v2 `jwtReplayCache` does not follow this pattern.

### Impact Explanation
A single valid, signed JWT authorizing a workflow trigger request is intended to be usable exactly once (`Authorize` explicitly documents/tests this as replay protection, and the error message states the JWT "has already been used"). Due to the non-atomic check-then-act pattern, an attacker (or a legitimate, unprivileged caller who merely races requests) can submit the same signed JWT+request pair concurrently and have the gateway accept it multiple times before the cache entry is recorded, each acceptance proceeding through `checkRateLimit` and on to triggering a real workflow execution via `setupCallback`/DON dispatch. This effectively bypasses the single-use authorization guarantee, resulting in duplicate/unauthorized workflow executions from one authorized signature — an authentication/replay-protection bypass on an internet-facing gateway endpoint.

### Likelihood Explanation
Exploitation only requires sending the same already-obtained, still-valid JWT in a short burst of parallel HTTP requests to the gateway's trigger endpoint; no privileged access or node compromise is needed. The race window is small but real given `isReplay`/`recordUsage` are separate lock acquisitions with authorization/signature-verification work executed in between, widening the window.

### Recommendation
Merge the check and the record into a single atomic operation performed under one lock (mirroring `RequestReplayGuard.CheckAndRecord` in `core/capabilities/vault/request_replay_guard.go`), e.g. a `jwtReplayCache.checkAndRecord(jti)` that acquires the write lock once, tests for existence, and inserts the entry before releasing the lock, rejecting the request atomically if already present.

### Proof of Concept
1. Obtain a valid signed workflow-trigger JWT for an authorized workflow signer.
2. Fire N (e.g. 20) concurrent HTTP requests to the gateway's HTTP-trigger endpoint using the identical JWT/request payload.
3. Observe that more than one request passes `WorkflowMetadataHandler.Authorize` (i.e., more than one workflow execution is dispatched to the DON via `setupCallback`), instead of exactly one succeeding and the rest failing with "JWT token has already been used."

### Citations

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
