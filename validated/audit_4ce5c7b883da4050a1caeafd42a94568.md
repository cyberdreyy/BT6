### Title
Concurrent map read/write in `WorkflowMetadataHandler.Authorize` causes a crash (panic) on unprivileged HTTP trigger requests - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
`WorkflowMetadataHandler` maintains `h.authorizedKeys` (a `map[string]map[gateway.AuthorizedKey]struct{}`) that is periodically rebuilt and swapped by `syncMetadata`, which correctly takes `h.mu.Lock()` before reassigning the map. However, `Authorize`, which is invoked on every unprivileged HTTP trigger request via `httpTriggerHandler.authorizeRequest` → `HandleUserTriggerRequest`, reads `h.authorizedKeys[workflowID]` directly with no lock at all. [1](#0-0) 

### Finding Description
`syncMetadata` runs on a periodic ticker (`MetadataAggregationIntervalMs`) and writes to `h.authorizedKeys`, `h.workflowRefToID`, `h.workflowIDToRef`, and `h.workflowShards` while holding `h.mu.Lock()`. [2](#0-1) 

`Authorize`, by contrast, is called synchronously from the gateway's user-facing HTTP trigger path (`httpTriggerHandler.authorizeRequest`) with no `h.mu.RLock()`/`RUnlock()` around its map read of `h.authorizedKeys[workflowID]`: [1](#0-0) [3](#0-2) 

This is the same root-cause pattern as the referenced ITSHub bug: a piece of shared state (a map/balance) is updated asynchronously by one code path (periodic metadata sync / token-balance update) while another code path (message processing / trigger authorization) reads that same state without any coordination or invariant check on freshness. In the ITS case, the mismatch produced financial loss; in this Go code, concurrent unsynchronized map access on the same underlying `map` object (one goroutine writing an entirely new map to `h.authorizedKeys`, another reading the old/new map value and indexing into it during a swap) is undefined behavior in Go and will panic with "fatal error: concurrent map read and map write" if a read races precisely with the map reference update, or with iteration if writes ever occur in-place instead of the current wholesale reassignment. Because `syncMetadata` runs continuously on a fixed interval for the lifetime of the gateway process, and `Authorize` is triggered by every single external HTTP trigger request (fully unprivileged, internet-facing), this race is reachable at will by any client that can reach the gateway's `HandleJSONRPCUserMessage`/`HTTPTriggerHandler` endpoint.

### Impact Explanation
A crash of the gateway process caused by concurrent, unsynchronized map access is a process-level denial of service — the gateway is an internet-facing entry point that other unprivileged workflow owners' HTTP triggers, actions, and metadata sync depend on. This matches the report's "DoS" classification, though impact here is bounded to availability (process crash / restart), not fund loss, since no balance or authorization state is exposed to escalation — the panic is a crash, not silently returning a wrong (permissive) authorization decision.

### Likelihood Explanation
The race window is opened on every `MetadataAggregationIntervalMs` tick for the life of the process, and the read side fires on every user HTTP trigger request. Under moderate to high load, or with more shards/more metadata volume increasing `syncMetadata` duration, the probability of the reader observing/racing on the map during the exact reassignment (or more concerning, the compiler/runtime detecting concurrent access with `-race` or `GODEBUG` map checks) is realistic without requiring any special positioning, front-running, or malicious peer/node behavior — any external client hitting the HTTP Trigger endpoint at the wrong moment can be involved.

### Recommendation
Guard the read in `Authorize` with `h.mu.RLock()`/`RUnlock()` (and ideally hold the lock across both the lookup and the subsequent map access) to match the locking discipline already used by `WorkflowShards`, `GetWorkflowID`, and `GetWorkflowReference` in the same file: [4](#0-3) [5](#0-4) 

### Proof of Concept
1. Start the gateway with `MetadataAggregationIntervalMs` set low (e.g., 10ms) and register at least one workflow so `syncMetadata` regularly rebuilds and reassigns `h.authorizedKeys`.
2. Concurrently, drive a tight loop of unprivileged `workflows.execute` HTTP trigger requests against the gateway (any external caller can do this; no auth beyond a valid JWT for the target workflow is needed to reach the `Authorize` code path — even invalid workflow IDs reach the `h.authorizedKeys[workflowID]` read).
3. Run both concurrently under `go test -race` (or in production, under sustained load) — the unguarded read in `Authorize` at `workflow_metadata_handler.go:92` races with the unguarded write in `syncMetadata` at `workflow_metadata_handler.go:178`, and Go's runtime will report "concurrent map read and map write" and crash the process (or the race detector will flag the exact data race), confirming reachable, unprivileged-triggerable memory-model violation and DoS.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-96)
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L271-278)
```go
func (h *WorkflowMetadataHandler) WorkflowShards(workflowID string) []*shardEndpoint {
	h.mu.RLock()
	defer h.mu.RUnlock()
	shards := h.workflowShards[workflowID]
	out := make([]*shardEndpoint, len(shards))
	copy(out, shards)
	return out
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L356-369)
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
