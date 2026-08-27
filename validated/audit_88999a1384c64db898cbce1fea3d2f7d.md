### Title
TOCTOU race between JWT replay check and recordUsage allows double execution of a single-use workflow trigger token - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` calls `h.jwtCache.isReplay(claims.ID)` and, only after several unrelated checks, `h.jwtCache.recordUsage(claims.ID)`, with each call taking and releasing its own lock independently rather than performing an atomic check-and-set. Two concurrent `Authorize` calls presenting the identical JWT `jti` can both observe `isReplay == false` before either has recorded usage, allowing a single validly-signed JWT to authorize the workflow trigger twice.

### Finding Description
In `Authorize` [1](#0-0) , the replay-protection logic is:
1. `h.jwtCache.isReplay(claims.ID)` — takes `cache.mu.RLock()`, checks map membership, releases the lock [2](#0-1) .
2. Signature/workflow-ID/authorized-key checks are performed with no locking relation to the JWT cache.
3. `h.jwtCache.recordUsage(claims.ID)` — takes `cache.mu.Lock()`, writes the map entry, releases the lock [3](#0-2) .

Because steps 1 and 3 are two independent critical sections rather than one atomic check-and-set, if two goroutines call `Authorize` with the same `token` (same `jti`) concurrently, both can execute `isReplay` before either executes `recordUsage`. Both will see `exists == false`, both will pass the authorized-key checks (since they use the same valid signer/key), and both will subsequently call `recordUsage`, both succeeding and returning a valid `*gateway.AuthorizedKey`. The intended one-shot replay protection is bypassed under concurrency.

This is a genuine TOCTOU (race) rather than merely a design choice, since `isReplay`+`recordUsage` are explicitly meant to function as an atomic "check-not-used-then-mark-used" guard per the comment "jwtReplayCache manages used JWT IDs to prevent replay attacks" [4](#0-3) .

### Impact Explanation
This breaks the request-binding/anti-replay guarantee of the single-use JWT: an attacker in possession of one valid signed request (e.g., by capturing/observing a single legitimately signed workflow-trigger JWT in transit or via any means an attacker can replay a request) can fire it twice (or more) concurrently and get the workflow trigger executed multiple times instead of once. Depending on what the workflow trigger does downstream (e.g., firing an on-chain transaction, executing a paid workflow action, or any state-changing effect gated on "one execution per signed authorization"), this can result in duplicate unauthorized job execution — matching the "unauthorized job run" / authentication-soundness impact class for REQUEST_BINDING.

### Likelihood Explanation
Exploitation requires only a single valid (previously captured or replayed) signed JWT for a workflow trigger request and the ability to send two requests to the gateway at (nearly) the same time — a trivial precondition for any external/unprivileged client capable of sending HTTP/gateway requests. No elevated credentials beyond a single valid signed token are needed, and the race window (RLock release to Lock acquire) is realistically wide open under any concurrent load, making the race reliably reproducible in a unit test using two goroutines and a sync barrier.

### Recommendation
Replace the separate `isReplay`/`recordUsage` calls with a single atomic check-and-set operation under one lock, e.g., add a method `checkAndRecord(jti string) bool` that takes `cache.mu.Lock()` once, checks for existence, and if absent inserts the timestamp and returns "not a replay," returning "replay" otherwise. Call this single atomic method in `Authorize` instead of the two separate steps.

### Proof of Concept
Go concurrency test in `workflow_metadata_handler_test.go`:
1. Construct a `WorkflowMetadataHandler` with a workflow ID registered and an authorized signer key, matching what `TestAuthorize`-style tests already set up.
2. Generate one valid signed JWT (`token`) with a fixed `jti` for that workflow/signer, per the existing test helpers used for `Authorize`.
3. Launch two goroutines simultaneously (synchronized via a `sync.WaitGroup`/start channel) that both call `h.Authorize(workflowID, token, req)` with the identical `token`.
4. Collect both results; assert that exactly one call returns `(key, nil)` and the other returns the "JWT token has already been used" error.
5. Run with `go test -race -run TestAuthorize_ConcurrentReplay` repeated multiple times (e.g., `-count=100`) to demonstrate both calls intermittently succeed under the current code, and confirm the fix (atomic check-and-set) makes exactly one succeed consistently.

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-405)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L407-412)
```go
func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```
