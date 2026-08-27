### Title
Data race on `WorkflowMetadataHandler.authorizedKeys` allows unsynchronized read during concurrent metadata sync - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` reads `h.authorizedKeys[workflowID]` without holding `h.mu`, while a background ticker goroutine (`syncMetadata`) periodically replaces `h.authorizedKeys` under `h.mu.Lock()`. This is the same bug class as the Celo commit referenced in the report: a shared field (`core.current` there, `h.authorizedKeys` here) is read from one goroutine without synchronization while written from another goroutine holding a mutex, producing a data race.

### Finding Description
`Authorize` is called on every incoming HTTP trigger request from an external, unprivileged workflow-triggering client to validate the JWT signer against the workflow's authorized keys: [1](#0-0) 

Note line 92 (`keys, exists := h.authorizedKeys[workflowID]`) accesses the map directly with no `h.mu.RLock()`/`RUnlock()`, unlike every other accessor of `h`'s protected state in this file (`WorkflowShards`, `GetWorkflowID`, `GetWorkflowReference` all correctly take `h.mu.RLock()`): [2](#0-1) [3](#0-2) 

Meanwhile, `syncMetadata` — run periodically via `runTicker` on the handler's own background goroutine (`h.runTicker(time.Duration(h.config.MetadataAggregationIntervalMs)*time.Millisecond, h.syncMetadata)`) — reassigns `h.authorizedKeys` to a brand-new map under `h.mu.Lock()`: [4](#0-3) [5](#0-4) 

Because `Authorize` runs concurrently on request-handling goroutines spawned per incoming HTTP trigger message, while `syncMetadata` runs on its own ticker goroutine and mutates `h.authorizedKeys` under lock, this is a classic unsynchronized read/write race — structurally identical to the Celo `core.current` bug (unlocked read racing with a mutex-protected write of the same field from a different goroutine).

### Impact Explanation
Under Go's memory model, this is undefined behavior detectable by `go test -race` and can manifest as a crash (`fatal error: concurrent map read and map write` since `syncMetadata` also iterates/writes nested maps), or a corrupted/stale read that could momentarily authorize or deny a signer inconsistently with the actual `authorizedKeys` map contents. Since this is on the authentication path for HTTP-triggered workflow executions from external requesters, a race-induced anomaly here touches request authentication for the gateway's HTTP trigger capability.

### Likelihood Explanation
`syncMetadata` runs on every `MetadataAggregationIntervalMs` tick (default appears to be on the order of a minute per the README), so the write races with the read on essentially every trigger request that arrives near a sync boundary; likelihood of triggering the race is moderate to high under production HTTP-trigger traffic combined with periodic metadata syncs, and reliably reproducible via `go test -race` with concurrent `Authorize` and `syncMetadata` calls.

### Recommendation
Acquire `h.mu.RLock()`/`h.mu.RUnlock()` around the `h.authorizedKeys[workflowID]` lookup in `Authorize`, consistent with the pattern already used in `WorkflowShards`, `GetWorkflowID`, and `GetWorkflowReference`.

### Proof of Concept
1. Construct a `WorkflowMetadataHandler` with at least one shard and start it so the `MetadataAggregationIntervalMs` ticker runs `syncMetadata` in a loop.
2. From one goroutine, repeatedly call `h.Authorize(workflowID, token, req)` with a valid signed JWT for a registered workflow ID.
3. From another goroutine (simulating the aggregation ticker, or by shortening `MetadataAggregationIntervalMs` in tests), repeatedly call `h.syncMetadata(ctx)`.
4. Run under `go test -race`; the race detector flags the unsynchronized read of `h.authorizedKeys` at line 92 against the mutex-protected write at line 178. [6](#0-5)

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L290-296)
```go
		h.runTicker(time.Duration(h.config.MetadataPullIntervalMs)*time.Millisecond, func(ctx context.Context) {
			err2 := h.sendMetadataPullRequest()
			if err2 != nil {
				h.lggr.Errorw("Failed to send pull request", "error", err2)
			}
		})
		h.runTicker(time.Duration(h.config.MetadataAggregationIntervalMs)*time.Millisecond, h.syncMetadata)
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
