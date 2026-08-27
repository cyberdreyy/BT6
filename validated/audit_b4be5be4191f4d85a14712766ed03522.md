### Title
JWT replay-cache check-then-record race allows request-authentication replay under concurrency - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
`WorkflowMetadataHandler.Authorize` enforces JWT single-use (anti-replay) semantics for HTTP-trigger requests by calling `h.jwtCache.isReplay(claims.ID)` and, only after signer/authorization checks succeed, calling `h.jwtCache.recordUsage(claims.ID)` [1](#0-0) . These are two independent, non-atomic operations on the `jwtReplayCache`, mirroring the Yieldy `Staking.sol` bug pattern where a "cycle" gate is *checked* separately from where it is later *updated* (`canBatchTransactions` check vs. `lastTokeCycleIndex = currentCycleIndex` write), leaving a window in which multiple actions pass the same gate before the state update lands.

### Finding Description
The replay cache is defined as a simple map guarded by its own mutex: `jwtReplayCache{mu sync.RWMutex, cache map[string]time.Time}` [2](#0-1) . In `Authorize`, the sequence is:

1. Verify JWT signature/claims.
2. Call `h.jwtCache.isReplay(claims.ID)` — a read that returns false if the `jti` hasn't been recorded yet.
3. Look up `authorizedKeys[workflowID]` and validate the signer.
4. Only after all of that succeeds, call `h.jwtCache.recordUsage(claims.ID)` to mark the `jti` as used [3](#0-2) .

Because `isReplay` (read) and `recordUsage` (write) are separate, unsynchronized calls rather than a single atomic check-and-set, two (or more) HTTP-trigger requests carrying the identical JWT (`jti`) can be processed concurrently: both can pass `isReplay` == false before either has called `recordUsage`, since neither request holds a lock across the whole check-then-set sequence. This is structurally identical to the reported bug class: a per-cycle gate (`currentCycleIndex > lastTokeCycleIndex`) that is read at one point and only updated at another, allowing multiple withdrawal requests to slip through the same "cycle" before the gate is closed.

### Impact Explanation
An unprivileged workflow-trigger caller who can send two requests with the same JWT (e.g., retried by a client, or deliberately duplicated by an attacker who intercepted/replayed a legitimate signed JWT) can defeat the intended single-use/anti-replay guarantee. Since `Authorize` gates dispatch of the HTTP trigger request to all DON members (workflow execution), a successful replay could cause the workflow to be triggered more than once from what should be a single-use, one-time token — an authentication/replay-protection bypass in an internet-facing gateway handler. This does not itself grant broader unauthorized access (the signer must still be a legitimately authorized key for the workflow), but it undermines the explicit anti-replay control and can cause duplicate/unintended job runs.

### Likelihood Explanation
Likelihood is moderate: the race window is narrow (map read vs. map write, both fast operations), but the gateway is designed for concurrent request handling from workflow nodes/users, and an attacker or misbehaving client controls the timing of duplicate submissions. No special privileges are needed — only possession of one previously-issued, still-valid JWT for an authorized workflow signer, which is itself part of the normal unprivileged trigger-request flow.

### Recommendation
Make the check-and-record of `jti` atomic: hold the cache's write lock across both the "is this `jti` already used" check and the insertion, e.g. implement a single `jwtReplayCache.checkAndRecord(jti string) (alreadyUsed bool)` method that does the exists-check and map insertion under one `mu.Lock()`, and call it before proceeding with signer validation in `Authorize`.

### Proof of Concept
1. Obtain (or have a legitimate node issue) one valid, unexpired JWT for an authorized workflow signer.
2. Fire two HTTP trigger requests carrying this same JWT to the gateway at (nearly) the same time.
3. Both goroutines executing `Authorize` call `isReplay(claims.ID)` before either calls `recordUsage(claims.ID)`; both observe "not replayed" and proceed to pass authorization, since the authorized-key checks succeed for both, and `recordUsage` is only called afterward by each independently.
4. Both requests get dispatched to DON members as authorized trigger invocations, i.e., the JWT was used twice despite the intended single-use design.

Note: I could not view the exact bodies of `isReplay`/`recordUsage`/`newJWTReplayCache` (only the struct and call sites were retrieved before tool budget was exhausted); the analysis above is based on the confirmed call pattern in `Authorize` and the field layout of `jwtReplayCache`. If `recordUsage` is internally called with the same lock held across the whole `isReplay`+`recordUsage` sequence (not shown in the code I reviewed), this finding would not apply — this should be verified directly in the file.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L29-34)
```go
// jwtReplayCache manages used JWT IDs to prevent replay attacks
type jwtReplayCache struct {
	mu            sync.RWMutex
	cleanupPeriod time.Duration
	cache         map[string]time.Time // jti -> timestamp
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
