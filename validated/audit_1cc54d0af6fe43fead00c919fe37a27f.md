### Title
Unsynchronized map access in `WorkflowMetadataHandler.Authorize()` races with `syncMetadata()` write — ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize()` reads the `h.authorizedKeys` map without acquiring `h.mu` at all, while the periodic `syncMetadata()` goroutine replaces that same map under `h.mu.Lock()`. This is the same bug class as the reported `encodedBlobStore.DeleteEncodingRequest()` issue — reading a mutable field while a lock discipline meant to protect it is bypassed — but here the read path takes **no lock whatsoever**, which is strictly worse than the reported "RLock instead of Lock" case.

### Finding Description
`Authorize()` is invoked on the JWT-authorization path for HTTP-trigger workflow requests. It reads `h.authorizedKeys[workflowID]` directly: [1](#0-0) 

No `h.mu.RLock()`/`RUnlock()` is taken anywhere in this function, even though `h.authorizedKeys` is a `map[string]map[gateway.AuthorizedKey]struct{}` field declared on the struct alongside other fields explicitly protected by `h.mu`: [2](#0-1) 

Concurrently, `syncMetadata()` (run periodically to refresh workflow metadata) replaces the whole map under a write lock: [3](#0-2) 

Other readers of the same struct's fields correctly take `h.mu.RLock()` (e.g. `GetWorkflowID`, `GetWorkflowReference`): [4](#0-3) 

This confirms the lock is intended to guard `h.authorizedKeys`/`h.workflowIDToRef`/`h.workflowRefToID`, but `Authorize()` was written without it — a classic missing/insufficient-lock bug, directly analogous to the reported `RLock()`-should-be-`Lock()` disperser issue, except here the omission is total (no lock at all) on the read side.

### Impact Explanation
`Authorize()` is reachable from unprivileged, external actors: it is called whenever the gateway processes an HTTP-trigger JSON-RPC request carrying a JWT to authorize a workflow signer. Because `h.authorizedKeys` is a Go map and is concurrently read (unlocked) in `Authorize()` while being reassigned (locked) in `syncMetadata()`, this is a data race on a map. In Go, concurrent unsynchronized map read/write is undefined behavior and, when detected by the runtime, causes a `fatal error: concurrent map read and map write`, which crashes the entire gateway process — not just the goroutine. Since `syncMetadata()` runs periodically in the background for the lifetime of the handler, any external client sending an HTTP-trigger authorization request at the moment a sync cycle fires can trigger this crash, giving an unprivileged remote actor a reliable denial-of-service vector against the gateway.

### Likelihood Explanation
`syncMetadata()` runs on a recurring timer for the life of the process, so the write side of the race is always active. `Authorize()` runs on every incoming HTTP-trigger authorization request, which is attacker-controlled and can be sent repeatedly to widen the race window (e.g., high-rate polling), making the crash straightforward to trigger with no special privileges.

### Recommendation
Acquire `h.mu.RLock()`/`defer h.mu.RUnlock()` at the top of `Authorize()` before reading `h.authorizedKeys` (and copy out the needed key set while holding the lock), mirroring the pattern already used in `GetWorkflowID`/`GetWorkflowReference`.

### Proof of Concept
Not applicable in static review — the race is inherent to the code path structure (unlocked read in `Authorize()` vs. locked write in `syncMetadata()`), reproducible by running the Go race detector while concurrently invoking `Authorize()` and `syncMetadata()`.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L36-54)
```go
type WorkflowMetadataHandler struct {
	services.StateMachine
	lggr            logger.Logger
	mu              sync.RWMutex
	authorizedKeys  map[string]map[gateway.AuthorizedKey]struct{} // map of workflow ID to authorized keys
	workflowRefToID map[workflowReference]string                  // map of workflow reference to workflow ID
	workflowIDToRef map[string]workflowReference                  // map of workflow ID to workflow reference
	workflowShards  map[string][]*shardEndpoint                   // map of workflow ID to the shards it is assigned to (quorum reached)
	// aggs holds one WorkflowMetadataAggregator per shard, keyed by shard donID.
	aggs            map[string]*aggregation.WorkflowMetadataAggregator
	shards          []*shardEndpoint
	nodeAddrToShard map[string]*shardEndpoint
	config          ServiceConfig
	stopCh          services.StopChan
	metrics         *metrics.Metrics
	jwtCache        *jwtReplayCache // JWT replay protection cache
	wg              sync.WaitGroup
	startTime       time.Time // time when Start() was called
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-104)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L164-182)
```go
	h.mu.Lock()
	defer h.mu.Unlock()

	if len(h.workflowIDToRef) == 0 && len(workflowIDToRef) > 0 {
		latencyMs := time.Since(h.startTime).Milliseconds()
		h.metrics.RecordMetadataSyncStartupLatency(ctx, latencyMs, h.lggr)
	}
	// Log all registered workflow IDs
	workflowIDs := make([]string, 0, len(workflowIDToRef))
	for workflowID := range workflowIDToRef {
		workflowIDs = append(workflowIDs, workflowID)
	}
	h.lggr.Debugw("Synced workflow metadata", "workflowIDs", workflowIDs, "count", len(workflowIDs))

	h.authorizedKeys = authorizedKeys
	h.workflowRefToID = workflowRefToID
	h.workflowIDToRef = workflowIDToRef
	h.workflowShards = workflowShards
	h.metrics.RecordLoadedMetadataSize(ctx, int64(len(h.workflowIDToRef)), h.lggr)
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L356-376)
```go
func (h *WorkflowMetadataHandler) GetWorkflowID(workflowOwner, workflowName, workflowTag string) (string, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	workflowRef := workflowReference{
		workflowOwner: workflowOwner,
		workflowName:  workflowName,
		workflowTag:   workflowTag,
	}
	workflowID, exists := h.workflowRefToID[workflowRef]
	if !exists {
		return "", false
	}
	return workflowID, true
}

func (h *WorkflowMetadataHandler) GetWorkflowReference(workflowID string) (workflowReference, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	workflowRef, exists := h.workflowIDToRef[workflowID]
	return workflowRef, exists
}
```
