### Title
Stale authorized-key metadata allows continued use of a revoked/rotated workflow signing key for up to one cleanup interval - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
Chainlink's HTTP Trigger gateway authenticates inbound workflow-execution requests by verifying a request JWT against an in-memory `authorizedKeys` map that is populated purely from a periodically-synced, quorum-based observation cache. There is no mechanism analogous to the recommended "freshness threshold" from the referenced report: once a workflow's authorized-key observation reaches quorum, it remains valid for authentication until the aggregator's time-based `cleanupInterval` expires it, regardless of whether the underlying key has since been revoked or rotated by the workflow owner. This mirrors the `WrappedIbbtcEth` pattern of trusting a cached value that is only refreshed by an external/periodic process, with no check for staleness at the point of use.

### Finding Description
`WorkflowMetadataHandler.Authorize` performs authentication purely against `h.authorizedKeys[workflowID]`, a map populated by `syncMetadata`: [1](#0-0) 

`syncMetadata` fully rebuilds `authorizedKeys` from whatever the per-shard `WorkflowMetadataAggregator.Aggregate()` currently reports as having reached quorum (`f+1` matching observations): [2](#0-1) 

The aggregator only removes an observation when it has not been re-observed for `cleanupInterval` (`reapObservations`), driven by a periodic ticker — this is a pure age-based garbage-collection mechanism, not a security-oriented freshness check: [3](#0-2) 

Metadata updates reach the gateway only via two paths — a push on workflow registration events, or a periodic pull (default interval documented as 1 minute): [4](#0-3) 

Because `Authorize` never checks *when* the currently-cached `authorizedKeys[workflowID]` entry was last confirmed fresh (no `lastUpdated`/threshold check comparable to `balanceToShares`'s missing staleness guard in the referenced report), a key that has been revoked or rotated by the workflow owner (e.g., due to compromise) continues to authenticate successfully for any signer whose observation is still cached, until: (a) the periodic pull/push cycle completes, and (b) the aggregator's `reapObservations` cycle actually expires the stale digest. During this window the compromised/old key is functionally equivalent to the "outdated pricePerShare" in the original report — a value that is stale relative to ground truth but is trusted without a corner-case check.

### Impact Explanation
An attacker who obtains an old/revoked/rotated signing key for a workflow (e.g., via compromise of a previous key, or a race during key rotation) can continue to submit valid, unprivileged HTTP trigger requests (`gateway_common.MethodWorkflowExecute`) that pass `Authorize` and cause the DON to execute the workflow — an unauthorized job run — for as long as the stale cached authorization entry persists (bounded by `cleanupInterval`, and by the metadata pull interval when no push has yet occurred to overwrite it). This is a caching/staleness design gap directly analogous to the referenced report's root cause: a periodically-refreshed value is consumed for a security-relevant decision (authorization) without any explicit staleness bound enforced at the read site.

### Likelihood Explanation
Exploitation requires the attacker to already hold a private key that was, at some point, a valid authorized signer for the target workflow (subsequently revoked/rotated) — this is a realistic key-rotation/incident-response scenario (e.g., responding to key compromise) rather than a purely theoretical one. The window is bounded but non-trivial (on the order of the metadata sync/cleanup interval, e.g., minutes), which is the same "capped but real" risk profile the original report describes for the price-staleness bug.

### Recommendation
Introduce an explicit freshness/version check into the authorization path, analogous to the `priceUpdateThreshold` remedy in the referenced report:
- Track a `lastUpdated`/generation counter per workflow's authorized-key set and reject/short-circuit authorization if the cached data has not been refreshed within a configured threshold, rather than relying solely on the unrelated GC-oriented `cleanupInterval`.
- On explicit key rotation/removal events, actively invalidate the corresponding cached observation (and any threshold-satisfying digest) rather than waiting for passive time-based reaping.
- Consider forcing a synchronous re-sync (equivalent to the report's "Full" variant that pays the cost of an update when data is stale) before authorizing when the cache age exceeds a security-relevant threshold distinct from `cleanupInterval`.

### Proof of Concept
1. Workflow owner registers workflow `W` with signing key `K1`; `K1` reaches quorum and is cached in `authorizedKeys[W]` via `syncMetadata`. [5](#0-4) 
2. `K1` is compromised; the workflow owner rotates to `K2` and revokes `K1` at the workflow-registry level. Nodes begin reporting the new metadata, but the gateway's `authorizedKeys[W]` still contains `K1` until the next successful sync/aggregation cycle overwrites it, and until `reapObservations` expires the old digest (bounded by `cleanupInterval`). [6](#0-5) 
3. Within that window, an attacker holding `K1` submits an `HTTPTriggerRequest` for workflow `W` signed with `K1`; `Authorize` succeeds because `K1` is still present in the (stale) `authorizedKeys[W]` map: [7](#0-6) 
4. The gateway forwards the trigger to the DON and the workflow executes using a key that should no longer be trusted.

**Note on investigation limits:** I was not able to fully trace the exact default value of `CleanUpPeriodMs`/`cleanupInterval` in production config, nor confirm whether any additional out-of-band revocation push (distinct from the periodic pull) exists elsewhere in the codebase that might shrink this window further. This should be verified in a live/staging environment before treating the exposure window as precisely bounded.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L110-161)
```go
// syncMetadata aggregates the authorized keys and workflow selectors from each
// shard's WorkflowMetadataAggregator and updates the local cache. A workflow is
// considered assigned to a shard once that shard's aggregator reports it (i.e.
// F+1 of the shard's nodes observed it).
func (h *WorkflowMetadataHandler) syncMetadata(ctx context.Context) {
	authorizedKeys := make(map[string]map[gateway.AuthorizedKey]struct{})
	workflowRefToID := make(map[workflowReference]string)
	workflowIDToRef := make(map[string]workflowReference)
	workflowShards := make(map[string][]*shardEndpoint)

	for _, shard := range h.shards {
		agg := h.aggs[shard.donID]
		metadata := agg.Aggregate()
		for _, data := range metadata {
			workflowID := data.WorkflowSelector.WorkflowID
			workflowRef := workflowReference{
				workflowOwner: data.WorkflowSelector.WorkflowOwner,
				workflowName:  data.WorkflowSelector.WorkflowName,
				workflowTag:   data.WorkflowSelector.WorkflowTag,
			}

			// Case 1: this workflow ID was already registered. If the reference
			// matches, this is the same workflow reported by another shard —
			// append the shard to its fan-out list. If the reference differs,
			// it's a conflicting observation; drop it.
			if existingRef, idExists := workflowIDToRef[workflowID]; idExists {
				if existingRef == workflowRef {
					workflowShards[workflowID] = append(workflowShards[workflowID], shard)
				} else {
					h.lggr.Debugw("Duplicate workflow ID with conflicting reference, dropping",
						"workflowID", workflowID, "existingRef", existingRef, "conflictingRef", workflowRef)
				}
				continue
			}

			// Case 2: this workflow reference was already registered under a
			// different workflow ID. First-wins by reference; drop the duplicate.
			if _, refExists := workflowRefToID[workflowRef]; refExists {
				h.lggr.Debugw("Duplicate workflow reference found, dropping",
					"workflowRef", workflowRef, "workflowID", workflowID)
				continue
			}

			// Case 3: new workflow ID and reference — register it.
			workflowIDToRef[workflowID] = workflowRef
			workflowRefToID[workflowRef] = workflowID
			authorizedKeys[workflowID] = make(map[gateway.AuthorizedKey]struct{})
			for _, key := range data.AuthorizedKeys {
				authorizedKeys[workflowID][key] = struct{}{}
			}
			workflowShards[workflowID] = append(workflowShards[workflowID], shard)
		}
```

**File:** core/services/gateway/common/aggregation/workflow_metadata_aggregator.go (L50-99)
```go
func (agg *WorkflowMetadataAggregator) reapObservations(ctx context.Context) {
	agg.mu.Lock()
	defer agg.mu.Unlock()
	now := time.Now()
	var expiredCount int
	for node, digestObservedAt := range agg.observedAt {
		for digest, observedAt := range digestObservedAt {
			if now.Sub(observedAt) > agg.cleanupInterval {
				delete(agg.observedAt[node], digest)
				if len(agg.observedAt[node]) == 0 {
					delete(agg.observedAt, node)
				}
				_, ok := agg.observations[digest]
				if !ok {
					agg.lggr.Warnw("Observation digest not found in observations", "digest", digest, "node", node)
					continue
				}
				agg.observations[digest].nodes.Remove(node)
				if len(agg.observations[digest].nodes) == 0 {
					delete(agg.observations, digest)
				}
				expiredCount++
			}
		}
	}
	if expiredCount > 0 {
		agg.metrics.IncrementMetadataObservationsCleanUpCount(ctx, int64(expiredCount), agg.lggr)
		agg.lggr.Debugw("Removed expired callbacks", "count", expiredCount)
	}
	agg.metrics.RecordMetadataObservationsCount(ctx, int64(len(agg.observations)), agg.lggr)
}

func (agg *WorkflowMetadataAggregator) Start(ctx context.Context) error {
	return agg.StartOnce("WorkflowMetadataAggregator", func() error {
		agg.lggr.Info("Starting WorkflowMetadataAggregator")
		go func() {
			ticker := time.NewTicker(agg.cleanupInterval)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					agg.reapObservations(ctx)
				case <-agg.stopCh:
					return
				}
			}
		}()
		return nil
	})
}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L89-103)
```markdown
## 5. Auth Metadata Messages and Aggregation Logic

### 5.1 Metadata Collection Process

The system implements a workflow metadata collection and aggregation system to sync workflow metadata from workflow nodes to gateway nodes.
There are 2 flows:

#### 5.1.1 Metadata Push (Registration Events)
- **Trigger**: Workflow registration event
- **Process**: HTTP capability nodes push workflow metadata to gateway

#### 5.1.2 Metadata Pull (Periodic Sync)
- **Trigger**: Periodic timer (default 1 minute intervals)
- **Process**: Gateway requests metadata from all HTTP capability nodes, which respond with batches of workflow metadata.

```
