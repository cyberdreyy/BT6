### Title
JWT replay-cache TOCTOU race allows a single-use trigger JWT to be replayed - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
The Vault/HTTP-trigger gateway's JWT replay protection performs its "check" (`isReplay`) and "record" (`recordUsage`) as two separate, independently-locked operations rather than a single atomic check-and-set, mirroring the class of race the Lighthouse commit fixed (registering state before, not after, entering a concurrently-processed queue).

### Finding Description
`jwtReplayCache` guards against reuse of a workflow-execution JWT `jti` with two independently locked methods: [1](#0-0) 

`Authorize` calls these non-atomically: it calls `isReplay` (RLock) early, does authorization-key lookups in between, and only calls `recordUsage` (separate Lock) at the very end, after the signer/key checks succeed: [2](#0-1) 

Because `isReplay` and `recordUsage` are not combined into one atomic "check-and-record" critical section (unlike `RequestReplayGuard.CheckAndRecord` in the Vault handler, which correctly does the check and insert under a single `sync.Mutex` critical section: [3](#0-2) ), two concurrent `Authorize` calls carrying the identical JWT (same `jti`) can both pass the `isReplay` check before either one calls `recordUsage`. This is functionally the same race pattern as the Lighthouse bug: state is registered too late (after passing through processing) rather than atomically at admission time, allowing the same credential to be "dialed"/authorized twice.

### Impact Explanation
A single-use, time-bounded workflow-trigger JWT is meant to authorize exactly one gateway-routed trigger invocation. If the race is won, the same JWT can authorize two (or more) concurrent `HandleUserTriggerRequest`/`Authorize` calls, effectively bypassing the single-use replay guard for workflow triggers. This does not itself grant a new privilege (the JWT is still signed by an authorized key), but it undermines the intended anti-replay guarantee stated by the cache and the corresponding test `"duplicate JWT token and request ID"` in `http_trigger_handler_test.go`, whose invariant assumes only one execution per token.

### Likelihood Explanation
The race window is narrow (between the `RLock`/`RUnlock` in `isReplay` and the later `Lock` in `recordUsage`, with signer/key-lookup work happening in between under no lock), but it is trivially triggerable by an external caller sending the identical signed JWT request concurrently/in parallel (e.g., duplicate HTTP submissions, retried requests racing each other) — a normal unprivileged client action, not requiring any privileged or node-level access.

### Recommendation
Combine `isReplay` and `recordUsage` into a single atomic check-and-record operation (analogous to `RequestReplayGuard.CheckAndRecord`), performed under one lock acquisition in `Authorize`, so that the JWT `jti` is marked "seen" before any subsequent authorization logic can run concurrently for the same token.

### Proof of Concept
Not independently reproducible from static analysis alone (requires exercising the network-facing gateway `Authorize` path with two goroutines racing the same JWT, similar to the existing `TestRequestReplayGuard_ConcurrentAccess` pattern) — a `t.Run` test issuing two simultaneous `Authorize` calls with the same `jti` would demonstrate whether both can pass `isReplay` before either calls `recordUsage`.

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
