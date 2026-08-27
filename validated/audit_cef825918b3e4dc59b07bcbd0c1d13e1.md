### Title
Unbounded growth of shared `h.callbacks` map via authorized-but-never-completing trigger requests, gated only by rate limiter × CleanUpPeriodMs, not a concurrency cap - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`httpTriggerHandler.setupCallback` only rejects duplicate `requestID`s and never checks how many callbacks are already pending for a workflow/signer before adding a new entry to the shared `h.callbacks` map [1](#0-0) . Removal only happens on success (`HandleNodeTriggerResponse` → `cleanupCallback`) or via the age-based `reapExpiredCallbacks` sweep that runs every `CleanUpPeriodMs` and evicts entries older than `CleanUpPeriodMs` [2](#0-1) . The only admission control before an entry is created is `checkRateLimit`, a token-bucket rate limiter scoped per-workflow (`settings.ScopeWorkflow`), not a concurrency/quota cap [3](#0-2) .

### Finding Description
The flow for a trigger request is: `HandleUserTriggerRequest` → `validatedTriggerRequest` → `resolveWorkflowID` → `authorizeRequest` (JWT/key check) → `checkRateLimit` → `setupCallback` (adds to `h.callbacks`) → `sendWithRetries` (fans out to DON shards) [4](#0-3) .

`checkRateLimit` calls `h.userRateLimiter.AllowErr(ctx)`, a rate limiter constructed from `cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit` at gateway construction time [5](#0-4) . This is a steady-state RPS + burst limiter, not a bound on the number of *simultaneously outstanding* callbacks. Once a request passes authorization and the rate check, `setupCallback` inserts an entry into `h.callbacks` keyed only by `requestID`, with the sole existing guard being rejection of an already-in-flight identical `requestID` [6](#0-5) . An entry is only removed when a shard reaches quorum and calls `cleanupCallback` from `HandleNodeTriggerResponse`, or when `reapExpiredCallbacks` deletes it after it exceeds `CleanUpPeriodMs` age [7](#0-6) .

If nodes never respond (e.g., unreachable, or responses fail to aggregate/authenticate), the entry survives until the next reap cycle. Because the only throttle on entry-creation is a steady RPS rate limiter (not a cap on total pending entries), the maximum number of concurrently pending callbacks a single authorized workflow/signer can hold in the shared `h.callbacks` map is proportional to `workflowRPS × CleanUpPeriodMs` (default `CleanUpPeriodMs` is 600000 ms / 10 minutes per the handler's README defaults) [8](#0-7) . Since `h.callbacks` is a single shared map guarded by one `sync.Mutex` for the entire gateway handler instance (all workflows/signers share it) [9](#0-8) , one authorized key sustaining traffic at its allowed per-workflow rate for the full reap window can grow the shared map to a size gated only by `rate × CleanUpPeriodMs`, with no independent per-workflow/per-signer cap enforced on the number of concurrently in-flight callbacks. Each entry additionally holds a `responseAggregators` map and a `doneCh`, and each request spawns retry goroutines (`sendWithRetries`/`sendToShard`) that hold shard `connMgr` sends until `MaxTriggerRequestDurationMs` elapses, so sustained flooding also consumes goroutines and shard-connection-manager send slots during the retry window, in addition to the map memory itself [10](#0-9) .

### Impact Explanation
This is a resource-exhaustion / availability concern, not a data-confidentiality or fund-movement bug: an unprivileged holder of a single authorized workflow key can, without violating auth or rate-limit checks, keep the gateway's shared `callbacks` map and associated goroutines/aggregator objects populated up to `rate-limit × CleanUpPeriodMs` size for the full window between cleanup sweeps. Because the map and its mutex are shared across all workflows served by the same gateway handler instance, sustained growth increases gateway memory and lock-contention on `callbacksMu`, degrading service for other workflows/signers sharing the same gateway process — this maps to a gateway-wide Denial-of-Service / resource-exhaustion impact class rather than an isolation break of another user's data.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only one authorized key for any single workflow (no admin/operator access). The attack is fully automatable — submit unique `requestID`s at up to the allowed per-workflow rate, ensuring requests never receive quorum responses (e.g., targeting a workflow whose assigned node(s) are slow/unreachable, or simply relying on natural node latency across `MaxTriggerRequestDurationMs`). It's a repeatable, sustained-traffic technique bounded only by the per-workflow rate limiter and reap interval, both of which are configuration values, not hard per-signer concurrency caps.

### Recommendation
Add an explicit, independent cap on the number of concurrently pending (in-flight) callbacks per workflow/signer (e.g., a resource-pool/concurrency limiter similar to `limits.MakeResourcePoolLimiter` used elsewhere for `PerWorkflow`/`PerOwner` execution concurrency) that is checked in `setupCallback` before insertion into `h.callbacks`, independent of `CleanUpPeriodMs` and the RPS rate limiter. Release the slot in `cleanupCallback`. Consider also capping total map size (`h.callbacks`) with a global ceiling to bound worst-case memory regardless of per-workflow counts.

### Proof of Concept
Go handler-level test plan (extending `http_trigger_handler_test.go`):
1. Build a `httpTriggerHandler` with `userRateLimiter` set to a permissive rate (e.g., `limits.WorkflowRateLimiter(highRPS, highBurst)`) and `CleanUpPeriodMs` set to a large value (e.g., 600000).
2. Configure a mock DON such that `SendToNode` never completes/never triggers `HandleNodeTriggerResponse` (simulate silent nodes).
3. In a loop, call `handler.HandleUserTriggerRequest` with unique `requestID`s for the same authorized `workflowID`/key, at a rate under the configured rate limit, for a simulated duration approaching `CleanUpPeriodMs`.
4. Assert via a test-only accessor (or by locking `callbacksMu` and reading `len(h.callbacks)`) that the number of pending entries for this single workflow grows unbounded by any concurrency cap and only shrinks after `reapExpiredCallbacks` fires — i.e., assert `len(h.callbacks) > expectedPerWorkflowConcurrencyLimit` at some point before the reap interval elapses, demonstrating no independent per-workflow/per-signer concurrency cap exists.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L53-66)
```go
type httpTriggerHandler struct {
	services.StateMachine
	config                  ServiceConfig
	shards                  []*shardEndpoint
	nodeAddrToShard         map[string]*shardEndpoint
	lggr                    logger.Logger
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
	stopCh                  services.StopChan
	workflowMetadataHandler *WorkflowMetadataHandler
	userRateLimiter         limits.RateLimiter
	metrics                 *metrics.Metrics
	wg                      sync.WaitGroup
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-140)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}

	strippedWorkflowID := strings.TrimPrefix(workflowID, "0x")
	legacyExecutionID, err := workflows.EncodeExecutionID(strippedWorkflowID, req.ID) //nolint:staticcheck // legacy ID kept for observability comparison
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID: " + err.Error())
	}
	// Workflows shouldn't use more than one HTTP trigger. If we ever need to support multiple triggers, we'd need to pass
	// trigger index to the Gateway handler and somehow allow senders to pick. For now, we use trigger index 0.
	// Execution IDs here are used only for logging.
	executionIDWithTriggerIndex, err := workflows.GenerateExecutionIDWithTriggerIndex(strippedWorkflowID, req.ID, 0)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error generating execution ID with trigger index: " + err.Error())
	}
	h.lggr.Debugw("processing request",
		"legacyExecutionID", legacyExecutionID,
		"executionIDWithTriggerIndex", executionIDWithTriggerIndex,
		"requestID", req.ID,
		"workflowID", workflowID)

	reqWithKey, err := reqWithAuthorizedKey(triggerReq, *key)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, internalErrorMessage, callback)
		return errors.New("error marshaling trigger request: " + err.Error())
	}

	doneCh, err := h.setupCallback(ctx, req.ID, callback, requestStartTime, workflowID)
	if err != nil {
		return err
	}

	return h.sendWithRetries(ctx, legacyExecutionID, executionIDWithTriggerIndex, reqWithKey, workflowID, doneCh)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L371-396)
```go
func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-435)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}

	// Build one response aggregator per shard the workflow is assigned to.
	assigned := h.workflowMetadataHandler.WorkflowShards(workflowID)
	if len(assigned) == 0 {
		// this shouldn't happen because we checked it in authorizeRequest()
		h.handleUserError(ctx, requestID, jsonrpc.ErrInternal, fmt.Sprintf("Workflow %s is not assigned to any DONs", workflowID), callback)
		return nil, errors.New("workflow is not assigned to any shards")
	}

	aggregators := make(map[string]*aggregation.IdenticalNodeResponseAggregator, len(assigned))
	for _, shard := range assigned {
		// (N+F)//2 + 1 threshold where N = number of nodes, F = number of faulty nodes
		threshold := (len(shard.members)+shard.f)/2 + 1
		agg, err := aggregation.NewIdenticalNodeResponseAggregator(threshold)
		if err != nil {
			return nil, errors.New("failed to create response aggregator: " + err.Error())
		}
		aggregators[shard.donID] = agg
	}

	doneCh := make(chan struct{})
	h.callbacks[requestID] = savedCallback{
		Callback:            callback,
		requestStartTime:    requestStartTime,
		createdAt:           time.Now(),
		responseAggregators: aggregators,
		doneCh:              doneCh,
	}
	return doneCh, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L439-446)
```go
func (h *httpTriggerHandler) cleanupCallback(requestID string) {
	saved, exists := h.callbacks[requestID]
	if !exists {
		return
	}
	close(saved.doneCh)
	delete(h.callbacks, requestID)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L526-544)
```go
// reapExpiredCallbacks removes callbacks that are older than the maximum age
func (h *httpTriggerHandler) reapExpiredCallbacks(ctx context.Context) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()
	now := time.Now()
	var expiredCount int
	for reqID, callback := range h.callbacks {
		if now.Sub(callback.createdAt) > time.Duration(h.config.CleanUpPeriodMs)*time.Millisecond {
			h.metrics.IncrementRequestErrors(ctx, jsonrpc.ErrInternal, h.lggr)
			h.cleanupCallback(reqID)
			expiredCount++
		}
	}
	if expiredCount > 0 {
		h.metrics.IncrementPendingRequestsCleanUpCount(ctx, int64(expiredCount), h.lggr)
		h.lggr.Infow("Removed expired callbacks", "count", expiredCount, "remaining", len(h.callbacks))
	}
	h.metrics.RecordPendingRequestsCount(ctx, int64(len(h.callbacks)), h.lggr)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L606-644)
```go
func (h *httpTriggerHandler) sendWithRetries(ctx context.Context, legacyExecutionID, executionIDWithTriggerIndex string, req *jsonrpc.Request[json.RawMessage], workflowID string, doneCh <-chan struct{}) error {
	if doneCh == nil {
		return errors.New("doneCh cannot be nil")
	}

	assigned := h.workflowMetadataHandler.WorkflowShards(workflowID)
	if len(assigned) == 0 {
		// this shouldn't happen because we checked it in authorizeRequest()
		h.callbacksMu.Lock()
		saved, exists := h.callbacks[req.ID]
		if exists {
			h.handleUserError(ctx, req.ID, jsonrpc.ErrInternal, fmt.Sprintf("Workflow %s is not assigned to any DONs", workflowID), saved.Callback)
			h.cleanupCallback(req.ID)
		}
		h.callbacksMu.Unlock()
		return fmt.Errorf("workflow %s not assigned to any shard", workflowID)
	}

	// Create a context that will be cancelled when the max request duration is reached
	maxDuration := time.Duration(h.config.MaxTriggerRequestDurationMs) * time.Millisecond
	ctxWithTimeout, cancel := context.WithTimeout(ctx, maxDuration)
	defer cancel()

	// Run one send loop per assigned shard in parallel.
	errCh := make(chan error, len(assigned))
	for _, shard := range assigned {
		h.wg.Go(func() {
			errCh <- h.sendToShard(ctxWithTimeout, shard, legacyExecutionID, executionIDWithTriggerIndex, req, doneCh)
		})
	}

	var combinedErr error
	for range assigned {
		if err := <-errCh; err != nil {
			combinedErr = errors.Join(combinedErr, err)
		}
	}
	return combinedErr
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L148-151)
```go
	userRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create user rate limiter: %w", err)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L159-171)
```markdown
### 6.4 Default Values

| Configuration | Default Value | Description |
|---------------|---------------|-------------|
| `CleanUpPeriodMs` | 600000 (10 min) | Cache and callback cleanup interval |
| `MaxTriggerRequestDurationMs` | 60000 (1 min) | Maximum time for trigger request processing |
| `MetadataPullIntervalMs` | 60000 (1 min) | Interval for pulling metadata from nodes |
| `MetadataAggregationIntervalMs` | 60000 (1 min) | Interval for aggregating collected metadata |
| `InitialIntervalMs` | 100 | Initial retry interval |
| `MaxIntervalTimeMs` | 30000 (30 sec) | Maximum retry interval |
| `Multiplier` | 2.0 | Exponential backoff multiplier |
| `OutboundRequestCacheTTLMs` | 600000 (10 min) | HTTP response cache TTL |

```
