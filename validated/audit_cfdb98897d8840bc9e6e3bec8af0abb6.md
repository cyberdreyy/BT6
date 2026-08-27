Found a concrete analog: a check-then-act race condition in the HTTP Trigger JWT replay guard, structurally analogous to the reported bug's root cause — a security-relevant state check and the corresponding state update are not performed atomically, allowing the guard to be bypassed under concurrent unprivileged requests.

### Title
JWT replay-guard check-and-record is non-atomic, allowing concurrent replay of a single-use JWT to bypass authorization - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` is meant to allow each JWT (identified by its `jti` claim) to authorize exactly one HTTP trigger request. It checks `jwtCache.isReplay(claims.ID)` and, if not a replay, later calls `jwtCache.recordUsage(claims.ID)` to mark the JWT as consumed. These are two separate, unsynchronized operations on the same map with distinct locks acquired and released independently, so two concurrent requests using the same JWT can both pass the `isReplay` check before either calls `recordUsage`.

### Finding Description
`Authorize` is invoked from the internet-facing HTTP trigger path when an unprivileged client submits a signed JWT to invoke a workflow via the gateway. The replay protection is implemented with two independently-locked methods: [1](#0-0) 

The caller performs the check and the record as two separate steps with authorization logic executed in between, holding no lock across the whole operation: [2](#0-1) 

This is the same class of bug as the reported `Staking.claim`/`Staking.update` issue: a piece of state (`balance` in the report, `jwtCache.cache[jti]` here) that is supposed to gate a security-relevant decision (rewards accounting vs. one-time JWT authorization) is read and written in a way that is not atomic with the actual decision logic, so a caller can race the check and get the decision applied twice (or, in the report's case, have the update silently dropped). Here, the vulnerability is: `isReplay` is checked (RLock, returns false), then the signer/authorized-key validation runs unlocked, and only afterward is `recordUsage` (Lock) called. Two goroutines processing the same JWT concurrently (e.g., the same signed JWT replayed to two gateway shards/handlers, or sent twice in quick succession) can both observe `isReplay == false` and both be authorized before either records usage.

### Impact Explanation
A single-use JWT intended to authorize exactly one workflow trigger invocation can be used to authorize more than one concurrent invocation, defeating the single-use/replay-prevention guarantee documented in the code (`JWTReplayPeriodMs`, `jwtReplayCache`). This is a request-impersonation/replay-bypass class issue: it allows an unprivileged caller to reuse a credential intended to be single-use to trigger extra unauthorized workflow executions within the race window, which can also multiply billing/metering side effects for a workflow owner. It matches the "concrete authentication or role bypass ... request impersonation ... allowlist or quota bypass" category from the validation rubric.

### Likelihood Explanation
Exploitation requires an attacker (or the legitimate holder of a valid JWT) to send the same JWT concurrently to the gateway (e.g., two parallel HTTP requests, or the same JWT replayed to a shard being processed simultaneously by two goroutines/requests) within the small time window between the `isReplay` read and the `recordUsage` write. This is a narrow race window per request, but is fully within an unprivileged client's control (they choose to fire concurrent requests), so likelihood is low-to-moderate — it requires deliberate timing but no privileged access, no cryptographic break, and no cooperation from the node operator.

### Recommendation
Make the check-and-record operation atomic under a single lock, e.g., add a method like `CheckAndRecord(jti string) bool` on `jwtReplayCache` that acquires the write lock once, checks presence, and inserts in the same critical section (mirroring the existing `RequestReplayGuard.CheckAndRecord` pattern already used in `core/capabilities/vault/request_replay_guard.go`), and have `Authorize` reject the request if the atomic check-and-record reports the JWT as already seen, before proceeding with authorized-key validation.

### Proof of Concept
1. Obtain (or intercept) a validly-signed JWT for a workflow trigger with `jti = X`.
2. Fire two (or more) concurrent HTTP requests to the gateway's HTTP trigger endpoint using the identical JWT.
3. Both requests reach `Authorize`; both call `h.jwtCache.isReplay(X)` before either calls `h.jwtCache.recordUsage(X)`, so both observe `isReplay == false`.
4. Both requests pass signer/authorized-key validation and are treated as separately authorized, resulting in two accepted trigger invocations from a single intended-single-use JWT. [2](#0-1) [1](#0-0)

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
