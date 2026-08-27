### Title
Gateway HTTP Trigger authorization relies on a periodically-synced, stale `authorizedKeys` snapshot, allowing revoked/rotated signer keys to remain valid - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
The external report's root cause is that `StabilizerNode.stabilize` makes a security/economically-critical decision using a `GlobalImpliedCollateralService` snapshot that is only refreshed (`syncGlobalCollateral()`) at fixed points, rather than being freshly computed right before the decision, creating a window where stale data drives an incorrect (and exploitable) outcome. The chainlink analog is `WorkflowMetadataHandler.Authorize`, which performs the security-critical JWT-signer authorization check against an in-memory `authorizedKeys` map that is likewise refreshed only on a fixed periodic cadence, not synchronously with the request being authorized.

### Finding Description
`WorkflowMetadataHandler` maintains `authorizedKeys map[string]map[gateway.AuthorizedKey]struct{}`, a cache mapping workflow ID to the set of ECDSA public keys allowed to sign trigger requests for that workflow: [1](#0-0) 

This cache is populated/replaced wholesale only by `syncMetadata`, which is invoked on a fixed ticker (`MetadataAggregationIntervalMs`), itself fed by a separate periodic pull ticker (`MetadataPullIntervalMs`): [2](#0-1) 

```go
h.runTicker(time.Duration(h.config.MetadataPullIntervalMs)*time.Millisecond, ...)
h.runTicker(time.Duration(h.config.MetadataAggregationIntervalMs)*time.Millisecond, h.syncMetadata)
```

`syncMetadata` fully replaces `h.authorizedKeys` under lock: [3](#0-2) 

Every incoming HTTP trigger request is authorized synchronously against this stale snapshot in `Authorize`, which is called directly from the unprivileged, internet-facing request path (`httpTriggerHandler.HandleUserTriggerRequest` → `authorizeRequest` → `workflowMetadataHandler.Authorize`): [4](#0-3) [5](#0-4) 

There is no mechanism to force a fresh, on-demand re-sync of a specific workflow's authorized keys at authorization time — unlike the malt fix, which calls `syncGlobalCollateral()` immediately before the value is consumed, the Gateway never re-pulls/re-aggregates metadata inline with a request; it strictly trusts whatever snapshot the last ticker tick produced.

### Impact Explanation
If a workflow owner rotates or revokes a compromised signer key (e.g., because a private key was leaked), the old key remains accepted by the Gateway for authorizing HTTP-triggered workflow executions until the next `MetadataPullIntervalMs` + `MetadataAggregationIntervalMs` cycle completes and quorum (`F+1`) is reached across the DON shard. During this window, an unprivileged holder of the old/revoked key can still submit valid JWT-signed `workflows.execute` trigger requests that pass `Authorize` and get dispatched to the workflow DON, i.e., unauthorized workflow execution triggering. This is a direct authentication/authorization-bypass analog to the "stale collateral data drives an incorrect economic decision" root cause in the source report — both are cases where a periodically-synced side cache, rather than data current to the moment of the security decision, gates a high-impact action.

### Likelihood Explanation
The condition is reachable purely through the normal, unprivileged HTTP Trigger request path — no operator or node-level privileges are required by the attacker holding the (formerly) valid key. The likelihood of the race window mattering depends on how promptly key rotation/revocation is expected to take effect versus the configured `MetadataPullIntervalMs`/`MetadataAggregationIntervalMs`; I was unable to fully verify (tool budget exhausted) whether the underlying `aggregation.WorkflowMetadataAggregator` additionally expires/evicts observations tied to a previously-valid but now-superseded key set faster than the next full sync, which would affect how large the exploitable window actually is.

### Recommendation
- Where a workflow's authorized-key set changes (rotation/revocation), trigger an immediate, targeted re-sync/invalidation of that workflow's entry in `authorizedKeys` rather than waiting for the next periodic ticker.
- Alternatively, treat `Authorize` similarly to the malt fix pattern: before accepting a signature, verify against the freshest available metadata (e.g., a short-TTL, on-demand-refreshed lookup) instead of a snapshot that can be arbitrarily stale relative to `MetadataPullIntervalMs`/`MetadataAggregationIntervalMs`.
- Document/bound the maximum staleness window and ensure it is short enough that key-revocation SLAs are met, and add explicit tests asserting a revoked key is rejected within that bound.

### Proof of Concept
Conceptual repro (not executed, based on code reading):
1. Workflow `W` is registered with authorized signer key `K1`; Gateway's `authorizedKeys[W] = {K1}`.
2. Operator rotates the workflow's signer to `K2` (compromise of `K1`) at time `T`.
3. Before the next `syncMetadata` tick (`T + MetadataPullIntervalMs + MetadataAggregationIntervalMs`), an attacker who still possesses `K1` submits a JWT-signed `workflows.execute` request for `W`.
4. `httpTriggerHandler.authorizeRequest` → `WorkflowMetadataHandler.Authorize` checks `h.authorizedKeys[W]`, which still contains `K1`, so the request is accepted and dispatched to the DON via `sendWithRetries`, despite `K1` having been revoked at the source of truth.

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L280-296)
```go
// Start begins the periodic pull loop.
func (h *WorkflowMetadataHandler) Start(ctx context.Context) error {
	return h.StartOnce("WorkflowMetadataHandler", func() error {
		h.lggr.Info("Starting HTTP Trigger Metadata Handler")
		h.startTime = time.Now()
		for _, shard := range h.shards {
			if err := h.aggs[shard.donID].Start(ctx); err != nil {
				return fmt.Errorf("failed to start aggregator for shard %s: %w", shard.donID, err)
			}
		}
		h.runTicker(time.Duration(h.config.MetadataPullIntervalMs)*time.Millisecond, func(ctx context.Context) {
			err2 := h.sendMetadataPullRequest()
			if err2 != nil {
				h.lggr.Errorw("Failed to send pull request", "error", err2)
			}
		})
		h.runTicker(time.Duration(h.config.MetadataAggregationIntervalMs)*time.Millisecond, h.syncMetadata)
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
