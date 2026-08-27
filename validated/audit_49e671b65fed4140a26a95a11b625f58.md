### Title
Unsynchronized read of `authorizedKeys` cache in `WorkflowMetadataHandler.Authorize` causes concurrent map access panic (gateway DoS) - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize()` reads the `authorizedKeys` cache without holding `h.mu`, while a background goroutine (`syncMetadata`) periodically replaces the entire map under `h.mu.Lock()`. This is the same underlying bug class as the reported issue — a security-critical operation (authorization) executes against a shared cache with no guarantee that the cache read is stable/synced, opening a window where the cache is mutated mid-read.

### Finding Description
`WorkflowMetadataHandler` stores per-workflow authorized signer keys in `h.authorizedKeys map[string]map[gateway.AuthorizedKey]struct{}`, protected in principle by `h.mu sync.RWMutex`.

The periodic sync routine rebuilds and swaps this map wholesale under the lock: [1](#0-0) 

However, the authorization check that is invoked on every incoming (unauthenticated, pre-auth) HTTP trigger request reads directly from `h.authorizedKeys` without acquiring `h.mu.RLock()`: [2](#0-1) 

`Authorize` is reached from the fully external, unprivileged request path: `gatewayHandler.HandleJSONRPCUserMessage` → `triggerHandler.HandleUserTriggerRequest` → `WorkflowMetadataHandler.Authorize`. [3](#0-2) 

`syncMetadata` runs on its own ticker (`MetadataAggregationIntervalMs`, default 1 minute) for the lifetime of the gateway: [4](#0-3) 

Because Go maps are not safe for concurrent read/write, any external caller who sends HTTP-trigger requests can race the periodic `syncMetadata` map swap. When the unsynchronized read in `Authorize` (`keys, exists := h.authorizedKeys[workflowID]`) overlaps with the locked write in `syncMetadata` (`h.authorizedKeys = authorizedKeys`), the Go runtime detects the concurrent map access and raises `fatal error: concurrent map read and write`, which is unrecoverable and terminates the entire gateway process — unlike the on-chain report where the impact was "wrong operator used," here the impact is a full process crash.

### Impact Explanation
Any unprivileged client sending HTTP trigger requests to the gateway can crash the entire gateway process (denial of service for all workflows and all DON members served by that gateway instance), simply by sending requests during the periodic metadata sync window, which recurs by default every minute for the lifetime of the process. This affects availability of the internet-facing HTTP Trigger capability gateway for all tenants, not just the attacker's own workflow.

### Likelihood Explanation
The race window recurs deterministically every `MetadataAggregationIntervalMs` (default 60s), and normal/attacker traffic to `HandleJSONRPCUserMessage` naturally exercises `Authorize` on every incoming trigger request. No special privileges, valid signature, or authorized workflow are even required to reach the vulnerable read — `Authorize` is called before the request is proven legitimate, so even garbage/unauthorized trigger requests trigger the map read. An attacker only needs to send a moderate volume of trigger requests continuously to reliably hit the periodic sync window and crash the process.

### Recommendation
Acquire `h.mu.RLock()`/`RUnlock()` around all reads of `h.authorizedKeys` (and any other fields mutated by `syncMetadata`) in `Authorize`, mirroring the locking already used in `GetWorkflowID` and `GetWorkflowReference`. More generally, add a lint/test rule (e.g., Go race detector in CI for this package) to catch unsynchronized access to shared caches that are rebuilt by background goroutines.

### Proof of Concept
1. Run the gateway with a configured `MetadataAggregationIntervalMs` (default 60000ms) and at least one registered workflow with authorized keys populated via metadata push/pull.
2. Continuously send HTTP-trigger JSON-RPC requests (any workflow ID, valid or not) to `HandleJSONRPCUserMessage`, causing repeated calls into `WorkflowMetadataHandler.Authorize`, which reads `h.authorizedKeys[workflowID]` unguarded.
3. Concurrently, `syncMetadata` fires on its ticker and reassigns `h.authorizedKeys = authorizedKeys` under `h.mu.Lock()`.
4. Run with `-race` or under sufficient load; the concurrent unguarded read racing the locked write on the same map triggers Go's `fatal error: concurrent map read and write`, crashing the gateway process — verifiable by running the existing `workflow_metadata_handler_test.go`/`http_trigger_handler_test.go` suite with `go test -race` while simulating concurrent `Authorize` calls and `syncMetadata` invocations.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L391-401)
```go
func (h *gatewayHandler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback handlers.Callback) error {
	h.metrics.IncrementTriggerRequestCount(ctx, h.lggr)
	err := h.triggerHandler.HandleUserTriggerRequest(ctx, &req, callback, time.Now())
	if err != nil {
		h.lggr.Errorw("failed to handle user trigger request", "requestID",
			req.ID, "err", err)
		// error response is sent to the response channel by the trigger handler
		// so return nil after logging
	}
	return nil
}
```
