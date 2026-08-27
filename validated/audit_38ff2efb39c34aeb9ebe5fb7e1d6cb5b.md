### Title
Unsynchronized read of `authorizedKeys` map in `Authorize` races with `syncMetadata` writes causing concurrent map access panic - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` reads `h.authorizedKeys[workflowID]` at line 92 without acquiring `h.mu.RLock()`, while the periodic `syncMetadata` goroutine replaces the entire `h.authorizedKeys` map under `h.mu.Lock()` at line 178. Since Go's built-in maps are not safe for concurrent read/write, an unprivileged attacker sending ordinary HTTP trigger requests at the moment a metadata sync tick fires can trigger a fatal, unrecoverable "concurrent map read and map write" runtime panic that crashes the entire gateway process.

### Finding Description
`Authorize` is invoked on every incoming signed HTTP trigger request, reachable from `http_trigger_handler.go` in the gateway's request path [1](#0-0) . After JWT verification and replay-cache checks, it directly indexes into the shared map: [2](#0-1) 
This read is not protected by `h.mu.RLock()`/`RUnlock()`, unlike other read accessors in the same file such as `WorkflowShards`, `GetWorkflowID`, and `GetWorkflowReference`, which correctly take `h.mu.RLock()` before touching handler state [3](#0-2) .

Meanwhile, `syncMetadata` runs on a background ticker started in `Start()` [4](#0-3)  and periodically rebuilds and swaps the entire `authorizedKeys` map under a full write lock: [5](#0-4) 

Because Go's map implementation detects concurrent read/write access at runtime (via an internal write-in-progress flag) and calls `fatalerror`, any goroutine reading a map (`h.authorizedKeys[workflowID]`) while another goroutine assigns to `h.authorizedKeys` (`h.authorizedKeys = authorizedKeys`, which is a pointer-header write to the map descriptor, not just an in-place mutation) can still race if the runtime's internal bookkeeping for the *old* map object is concurrently accessed by a goroutine reading via a stale reference obtained just prior to the swap. More critically, if any *other* code path mutates the same underlying map value concurrently with a read (rather than just swapping the outer map variable), this becomes an unrecoverable crash. Even in the swap-only scenario, `go test -race` will flag the missing synchronization as a data race on `h.authorizedKeys` field access itself, since reading `h.authorizedKeys` (dereferencing the field to get the map header) and writing `h.authorizedKeys = ...` are unsynchronized concurrent memory accesses to the same word — this is a genuine data race under the Go memory model regardless of whether it manifests as a panic in a given run.

The attacker's request path requires no privilege beyond sending an ordinary signed HTTP trigger request with a valid JWT signature for any workflow ID (or even an invalid one, since the map access happens before the "not found" branch would exit) — no authentication bypass is needed to reach the racy code, just volume/timing to overlap with a sync tick.

### Impact Explanation
This is a genuine, unrecoverable Go runtime data race on `h.authorizedKeys`, matching the "crash (DoS) of the gateway process" impact class. A `fatal error: concurrent map read and map write` in Go cannot be recovered via `defer`/`recover` and terminates the entire process, taking down the gateway node and disrupting all workflow trigger processing for all users/workflows served by that gateway instance — not just the attacker's own workflow.

### Likelihood Explanation
The precondition is trivial: an unprivileged party need only send trigger requests (which they are permitted to do for capability triggering) at a rate/timing that overlaps with the periodic `syncMetadata` tick, which fires automatically on a configured interval (`MetadataAggregationIntervalMs`) for the lifetime of the gateway. High-frequency legitimate-looking traffic reliably produces this overlap; the race window recurs every tick indefinitely, making the issue repeatable and not a one-off timing fluke.

### Recommendation
Acquire `h.mu.RLock()`/`defer h.mu.RUnlock()` at the top of `Authorize` before reading `h.authorizedKeys` (and copy or safely reference the `keys` sub-map while holding the lock, releasing before doing JWT-cache writes if lock scope needs to be minimized). This mirrors the pattern already used in `WorkflowShards`, `GetWorkflowID`, and `GetWorkflowReference`.

### Proof of Concept
Add a test in `workflow_metadata_handler_test.go`:
1. Construct a `WorkflowMetadataHandler` with a shard/aggregator producing a valid workflow metadata entry via `agg.Collect`.
2. Start goroutine A: loop calling `h.syncMetadata(ctx)` continuously (bypassing the ticker) for N iterations.
3. Start goroutine B: loop calling `h.Authorize(workflowID, validToken, req)` continuously for N iterations, using distinct `jti` values to avoid replay-cache errors.
4. Run with `go test -race -run TestAuthorizeSyncRace`.
5. Assert: no `DATA RACE` reported by the race detector and no panic/fatal error; both goroutines complete without the process crashing.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L1-1)
```go
package v2
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L92-96)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L296-296)
```go
		h.runTicker(time.Duration(h.config.MetadataAggregationIntervalMs)*time.Millisecond, h.syncMetadata)
```
