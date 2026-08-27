### Title
Per-workflow-only HTTP trigger rate limiting allows owner-level quota bypass via workflow fragmentation - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`checkRateLimit` enforces `userRateLimiter.AllowErr` scoped only by `contexts.CRE{Owner, Workflow}`, but the limiter itself is constructed with a single workflow-scoped setting (`cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit`), with no owner-level bucket combined in. An owner controlling multiple registered workflowIDs can therefore multiply aggregate HTTP-trigger throughput linearly with the number of workflows they own.

### Finding Description
`h.userRateLimiter` is built in `NewGatewayHandler` via `lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)` [1](#0-0) , a single rate limiter keyed to the `PerWorkflow` settings scope. `checkRateLimit` then calls `h.userRateLimiter.AllowErr(ctx)` with `ctx` carrying both `Owner` and `Workflow` from `contexts.CRE{...}` [2](#0-1) , but the switch statement on `errLimited.Scope` only handles `settings.ScopeWorkflow`, confirming the limiter only ever reports/enforces a workflow-scoped bucket — there is no corresponding owner-scoped limiter combined via something like `MultiResourcePoolLimiter`, contrary to the pattern used elsewhere in the codebase (e.g. `syncerlimiter.NewWorkflowLimits` combines `owner` and `global` limiters [3](#0-2) , and `EngineLimiters.init` chains `wfExec, ownerExec, orgExec, globalExec` for execution concurrency [4](#0-3) ). Since each distinct `workflowID` gets its own independent rate-limit bucket, an owner authorized for N distinct workflows can trigger each at the per-workflow limit simultaneously, achieving N× the intended per-workflow throughput cap in aggregate for that owner.

### Impact Explanation
This is a quota-bypass affecting resource/financial exposure controls at the gateway: HTTP trigger throughput intended to be bounded is only bounded per-workflow, not per-owner, so an owner can scale total request volume (and downstream DON/node compute cost) by fragmenting load across multiple workflowIDs they control. This does not grant access to another user's resources or credentials — it only lets an owner exceed the aggregate throughput apparently intended by the rate-limiting design for their own account.

### Likelihood Explanation
Requires the attacker to already be authorized (own the run-role key/JWT) for multiple distinct workflowIDs under the same owner, and those workflows must be registered/assigned to a DON shard. Registering additional workflows is itself gated by a separate, unrelated control (`PerOwner.WorkflowLimit` in `syncerlimiter`, a registration-time cap), which somewhat bounds how far this can be fragmented, but does not close the gap for owners within that registration limit.

### Recommendation
Add an owner-scoped rate/resource limiter (e.g. `PerOwner.HTTPTrigger.RateLimit`) and combine it with the existing per-workflow limiter (analogous to `MultiResourcePoolLimiter` usage elsewhere) so `checkRateLimit` enforces both scopes, and extend the switch in `checkRateLimit` to handle `settings.ScopeOwner` explicitly for correct metrics/logging.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go`:
1. Construct a `userRateLimiter` with a restrictive `PerWorkflow.HTTPTrigger.RateLimit` (e.g. burst=1) using `limits.WorkflowRateLimiter(1, 0)` as in the existing rate-limit test [5](#0-4) .
2. Register two distinct workflowIDs under the same `workflowOwner` via `registerWorkflow` and `workflowIDToRef`.
3. Send one trigger request per workflowID in sequence; assert both succeed (no `ErrLimitExceeded`), proving the same owner obtained 2× the configured per-workflow burst by using two workflowIDs, whereas a single-workflow owner is capped at 1.
4. Assert that `errors.AsType[limits.ErrorRateLimited](err).Scope` is only ever `settings.ScopeWorkflow` across both calls, confirming no owner-level bucket is consulted or exhausted.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L148-151)
```go
	userRateLimiter, err := lf.MakeRateLimiter(cresettings.Default.PerWorkflow.HTTPTrigger.RateLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create user rate limiter: %w", err)
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

**File:** core/services/workflows/syncerlimiter/limiter.go (L60-77)
```go
	lf.Settings = keyedOwnerSettings{getter: lf.Settings, key: ownerLimit.Key, vals: perOwner}
	owner, err := limits.MakeResourcePoolLimiter(lf, ownerLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create owner resource limiter: %w", err)
	}

	globalLimit := cresettings.Default.WorkflowLimit // make a copy
	if cfg.Global > 0 {
		globalLimit.DefaultValue = int(cfg.Global)
	}
	global, err := limits.MakeResourcePoolLimiter(lf, globalLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create global resource limiter: %w", err)
	}

	lggr.Debugw("workflow limits set", "perOwner", cfg.PerOwner, "global", cfg.Global, "overrides", cfg.PerOwnerOverrides)

	return limits.MultiResourcePoolLimiter[int]{owner, global}, nil
```

**File:** core/services/workflows/v2/config.go (L171-179)
```go
	ownerExec, err := limits.MakeResourcePoolLimiter(lf, cresettings.Default.PerOwner.WorkflowExecutionConcurrencyLimit)
	if err != nil {
		return
	}
	wfExec, err := limits.MakeResourcePoolLimiter(lf, cfg.ExecutionConcurrencyLimit)
	if err != nil {
		return
	}
	l.ExecutionConcurrency = limits.MultiResourcePoolLimiter[int]{wfExec, ownerExec, orgExec, globalExec}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1973-1976)
```go
	t.Run("rate limit exceeded returns proper error", func(t *testing.T) {
		// Create a rate limiter with very restrictive limits
		restrictiveRateLimiter := limits.WorkflowRateLimiter(1, 0)
		handler := newTestTriggerHandler(t, lggr, cfg, donConfig, mockDon, metadataHandler, restrictiveRateLimiter, testMetrics)
```
