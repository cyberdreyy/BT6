## Title
JWT replay-protection check-then-act race allows single-use trigger token to be reused for multiple workflow executions - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
The gateway's HTTP trigger handler authorizes each incoming `workflows.execute` request with a JWT that is meant to be single-use, tracked via a replay cache keyed by the JWT's `jti` claim. The "check" (`isReplay`) and the "commit" (`recordUsage`) of that cache are two separate, non-atomic operations separated by other authorization logic. This is structurally the same class of bug as the reported `quorumVotes` issue: a security-critical value (whether the JWT id has already been consumed) is read once and only committed later, leaving a window in which the same JWT can pass the check multiple times before any usage is recorded — bypassing the intended one-token/one-execution invariant.

### Finding Description
`WorkflowMetadataHandler.Authorize` performs replay protection like this: [1](#0-0) 

The critical sequence is:
1. `h.jwtCache.isReplay(claims.ID)` — takes a read lock, checks map membership, releases the lock.
2. Unrelated work (looking up `authorizedKeys[workflowID]`, verifying signer is authorized).
3. `h.jwtCache.recordUsage(claims.ID)` — takes a write lock and inserts the `jti` only at the very end. [2](#0-1) 

Because steps 1 and 3 are not protected by a single critical section spanning the whole `Authorize` call, any unprivileged client holding one valid, signed trigger JWT can send that same JWT concurrently (e.g., N parallel HTTP requests hitting the gateway with identical `Auth`). Every concurrent request executes `isReplay` before any of them has called `recordUsage`, so all of them observe `exists == false` and are treated as fresh, unused tokens. Each one then proceeds through `authorizeRequest` in the trigger handler and is forwarded to the DON as a real workflow execution: [3](#0-2) 

The per-workflow rate limiter (`checkRateLimit`) is a separate, coarser control keyed by workflow owner/ID rather than by JWT, so it does not close this window; the JWT replay cache is the mechanism specifically intended to guarantee a signed request can only trigger one execution.

### Impact Explanation
This breaks the "a signed trigger request can only be used once" invariant, mirroring the reported class of bug where a value meant to gate an action (quorum / one-time-use) is computed/checked against stale state that can still change before it is durably recorded. Concretely:
- An unprivileged holder of one signed JWT can cause multiple duplicate workflow executions (unauthorized/duplicate job runs) on the target DON using a single authorization artifact, defeating the anti-replay control that callers and workflow owners rely on to bound execution to exactly the requests they signed.
- Depending on what the triggered workflow does (e.g., initiating on-chain actions, spending metered/paid capability quota, or moving funds through a downstream capability), this can translate into duplicated fund-moving actions or quota/metering bypass, entirely from an unprivileged client-facing request path (the gateway's public trigger endpoint).

### Likelihood Explanation
The race window is trivially reachable by any external client that already possesses one valid signed JWT for a workflow (which is the normal, expected credential for calling the trigger API) — no privileged or node-level access is required. Firing several requests with the same `Auth` value in parallel (a common, cheap client-side technique) is enough to hit the window; no clock manipulation, block timing, or special protocol knowledge is needed, unlike many blockchain-specific race conditions.

### Recommendation
Make the replay check-and-mark atomic: perform the "is this `jti` new?" test and the insertion into the cache under a single write-lock-held critical section (e.g., a `CheckAndRecord(jti) bool` method that acquires `cache.mu.Lock()` once, checks for existence, and inserts if absent, returning whether it was newly inserted). Reject the request only if this atomic operation reports the `jti` was already present, and perform this immediately when the JWT is verified rather than after other authorization steps.

### Proof of Concept
1. Obtain one valid signed JWT for a workflow trigger (as any legitimate caller would).
2. Build a `workflows.execute` JSON-RPC request with `Auth` set to that JWT.
3. Fire, e.g., 10 identical requests concurrently at the gateway's HTTP trigger endpoint (same `req.Auth`, distinct `req.ID` per JSON-RPC requirements).
4. Because `isReplay` and `recordUsage` are not atomic, more than one of the concurrent goroutines calling `WorkflowMetadataHandler.Authorize` will observe `isReplay == false` before `recordUsage` completes for the winning request, so more than one request is authorized and forwarded via `sendWithRetries` to the DON, resulting in multiple executions from a single supposedly single-use token.

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
