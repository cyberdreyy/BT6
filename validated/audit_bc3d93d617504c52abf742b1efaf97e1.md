### Title
JWT replay protection is time-window bound, not tied to token `exp`, allowing replay after the JTI cache eviction window - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` (in `workflow_metadata_handler.go`) relies solely on `jwtReplayCache` to reject reused JWTs, and that cache evicts JTI entries purely based on wall-clock age (`JWTReplayPeriodMs`) with no cross-check against the JWT's own `exp` claim. If a JWT is minted with a long-lived or absent expiry, an attacker who captures a previously-used, validly-signed JWT can wait past the cache cleanup window and resubmit it to `HandleUserTriggerRequest` → `authorizeRequest` → `Authorize`, which will accept it as if it were fresh.

### Finding Description
The reachable path is: a gateway user submits an HTTP trigger request (`workflows.execute`) via `httpTriggerHandler.HandleUserTriggerRequest` [1](#0-0) , which calls `authorizeRequest` → `WorkflowMetadataHandler.Authorize(workflowID, req.Auth, req)` [2](#0-1) .

Inside `Authorize`, replay protection is implemented as: verify JWT signature/claims via `utils.VerifyRequestJWT`, then check `h.jwtCache.isReplay(claims.ID)`, and if not a replay, `h.jwtCache.recordUsage(claims.ID)` [3](#0-2) .

The cache itself is a simple `map[string]time.Time` keyed by `jti`, and entries are purged solely by a periodic ticker calling `cleanupOldEntries(now.Add(-cleanupPeriod))`, which deletes any entry whose `recordUsage` timestamp is older than `cleanupPeriod` (`JWTReplayPeriodMs`) [4](#0-3) [5](#0-4) . Once a `jti` is evicted, `isReplay` returns `false` for that same JTI again [6](#0-5) .

There is no code path that ties this cleanup cutoff to the JWT's `exp` claim, nor any check within `Authorize` that rejects a JWT because it has already been used previously beyond the cache window — the only replay defense is cache membership. If the JWT itself was minted with no `exp` (or a distant one) and `JWTReplayPeriodMs` is smaller than the token's real validity lifetime (which is entirely up to whoever issued the JWT — the trigger caller, not the gateway), the token remains cryptographically valid and signer-authorized long after it is evicted from the replay cache, so a resubmission with the exact same `jti`/signature will pass `Authorize` a second time and trigger `HandleUserTriggerRequest` again, causing a duplicate unauthorized workflow execution to be dispatched to the DON shards.

### Impact Explanation
This is a genuine authentication/replay-protection soundness gap: an attacker holding a single valid signed JWT for a workflow trigger can obtain more than one execution from it, once the replay window elapses, by resubmitting the identical previously-used request. This causes duplicate/unauthorized pipeline execution (repeat on-chain or off-chain workflow triggering) beyond what the single signed authorization should permit — matching a duplicate/unauthorized job-run impact class.

### Likelihood Explanation
Exploitability requires only: (1) possession of a previously-submitted, validly-signed JWT for the target workflow (which the attacker already legitimately used once, or which they observed/captured), and (2) knowledge or measurement of `JWTReplayPeriodMs` (a fixed, low-entropy config value) so they know how long to wait. No elevated privileges beyond being a normal caller of the gateway's HTTP trigger endpoint are needed. Feasibility depends on the JWT issuer setting a long or absent `exp`; the gateway code does not enforce any tie between `exp` and the replay-cache window, so the strength of protection is fully dependent on external JWT-issuance discipline rather than an invariant enforced by this code. This makes the issue systemic (config/design gap) but concretely exploitable whenever `exp` outlives `JWTReplayPeriodMs`.

### Recommendation
Bind the effective replay window to the JWT's own `exp` claim rather than (or in addition to) a fixed wall-clock cache-cleanup window: reject tokens with no `exp` or with `exp` beyond an allowed max lifetime, and only evict a `jti` from the replay cache after its `exp` has passed (not simply after `JWTReplayPeriodMs` since first use). This guarantees a `jti` can never be replayed while it would still be considered a "fresh" authorization.

### Proof of Concept
Go unit test plan for `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go`:
1. Construct a `WorkflowMetadataHandler` with a `jwtReplayCache` created via `newJWTReplayCache(shortCleanupPeriod)` (e.g., 100ms) instead of the production duration.
2. Mint a JWT with `jti = "X"` and `exp` set far in the future (or omitted, if `VerifyRequestJWT` permits it), signed by an authorized key for a test `workflowID`.
3. Call `Authorize(workflowID, token, req)` once — assert success and that `jwtCache.cache["X"]` is populated.
4. Manually invoke `jwtCache.cleanupOldEntries(time.Now())` (simulating the passage of `cleanupPeriod`) to evict `"X"` from the cache, bypassing real wall-clock sleep.
5. Call `Authorize(workflowID, token, req)` again with the identical token/jti — assert that it currently **succeeds** (demonstrating the replay), whereas the expected secure behavior would be rejection because the token's `exp` has not elapsed.
6. Assert `HandleUserTriggerRequest` would be invoked twice for the same signed authorization, confirming duplicate trigger dispatch potential.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L298-304)
```go
		h.runTicker(h.jwtCache.cleanupPeriod, func(ctx context.Context) {
			now := time.Now()
			expiredCount := h.jwtCache.cleanupOldEntries(now.Add(-h.jwtCache.cleanupPeriod))
			h.metrics.IncrementJwtCacheCleanUpCount(ctx, int64(expiredCount), h.lggr)
			h.metrics.RecordJwtCacheSize(ctx, int64(len(h.jwtCache.cache)), h.lggr)
			h.lggr.Debugw("Workflow execution cache cleanup completed", "expired_entries", expiredCount, "remaining_entries", len(h.jwtCache.cache))
		})
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L414-426)
```go
// cleanupOldEntries removes expired entries from the cache
func (cache *jwtReplayCache) cleanupOldEntries(cutoff time.Time) int {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	var expiredCount int
	for jti, createdAt := range cache.cache {
		if createdAt.Before(cutoff) {
			delete(cache.cache, jti)
			expiredCount++
		}
	}
	return expiredCount
}
```
