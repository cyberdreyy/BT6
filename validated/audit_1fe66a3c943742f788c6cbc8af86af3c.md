### Title
Deducted metering credits are not refunded when a capability execution fails - ([File: core/services/workflows/v2/capability_executor.go])

### Summary
`callCapability` in `core/services/workflows/v2/capability_executor.go` earmarks (deducts) local billing balance for a capability call before invoking it, but on any capability execution failure it returns early without ever calling `meterReport.Settle`, so the deducted balance is never returned to the workflow's available credit pool.

### Finding Description
In `(*ExecutionHelper).callCapability`, when a metering report is present, the code calls `meterReport.Deduct(meteringRef, metering.ByDerivedAvailability(...))` [1](#0-0)  which earmarks part of the local credit balance for this step (`r.balance.Minus(limit.Decimal)` inside `ByDerivedAvailability`) [2](#0-1) .

The capability is then executed via `capability.Execute(execCtx, capReq)` [3](#0-2) . If it returns an error — whether a user error, a system error, or any other error — the function logs/emits an event and returns immediately [4](#0-3) . `meterReport.Settle` is only invoked in the success path [5](#0-4) .

`Settle` is the only mechanism that returns the difference between the earmarked `Deduction` and the actual spend back to the balance (`r.balance.Add(step.Deduction.Sub(spentCredits))`) [6](#0-5) . Since `Settle` requires a step recorded by `Deduct` and is guarded against being called twice (`ErrStepDeductExists` / `ErrStepSpendExists`) [7](#0-6) , there is no other code path in the repository that re-settles or refunds a step whose `Deduct` succeeded but whose capability call subsequently failed — `Settle` appears exactly once in `capability_executor.go` [5](#0-4) , and grepping the codebase shows no other call site that settles a specific `ref` after a failure.

This mirrors the reported bug class: funds/credits are taken from the user (deducted) up front, but if the downstream operation (capability execution) fails, they are never returned.

### Impact Explanation
Every failed capability call inside a workflow execution (timeout, user error, system error, capability-side failure) permanently consumes the earmarked local credit balance for that step, even though no actual capability spend occurred. Because balance is workflow-execution-scoped and capped by `Reserve`, this reduces the credits available for subsequent capability calls within the same execution and is unrecoverable within that execution's lifetime — repeated failures (e.g., a flaky or misbehaving capability) can exhaust an otherwise unspent balance, causing legitimate calls to be rejected via `ErrInsufficientBalance`. This is a fund/credit loss for the unprivileged workflow owner triggered purely by capability failures, not by actual resource consumption.

### Likelihood Explanation
High likelihood of being triggered: any capability call that returns an error (a common, expected occurrence — user errors, timeouts, transient system errors) hits this code path. No malicious action or privileged access is required; a normal workflow owner running a workflow that calls any capability can trigger it whenever that capability fails.

### Recommendation
In `callCapability`, ensure `meterReport.Settle` (or an equivalent refund path) is called on every exit path after `Deduct` has succeeded, including the error branches at lines 263-284, so the earmarked balance for a failed step is returned to the available pool (e.g., settle with zero/failed spend metadata, or introduce an explicit "Release"/"Refund" method mirroring the reserve/deduct/settle lifecycle).

### Proof of Concept
1. A workflow execution reserves and deducts credits per capability call via `meterReport.Deduct` before `capability.Execute` [1](#0-0) .
2. The workflow calls a capability that fails (e.g., returns any non-nil `err` from `capability.Execute`).
3. `callCapability` returns immediately in the error branch without calling `meterReport.Settle` [8](#0-7) .
4. The earmarked amount from `Deduct` is never returned to `r.balance` because only `Settle` performs the refund (`r.balance.Add(step.Deduction.Sub(spentCredits))`) [6](#0-5) , and it was never called for this `ref`.
5. Subsequent capability calls in the same execution have less available balance than they should, potentially failing with `ErrInsufficientBalance` even though no real spend occurred for the failed step.

### Citations

**File:** core/services/workflows/v2/capability_executor.go (L202-212)
```go
		if spendLimits, err = meterReport.Deduct(
			meteringRef,
			metering.ByDerivedAvailability(
				userSpendLimit,
				openConcurrentCallSlots,
				info,
				config.RestrictedConfig,
			),
		); err != nil {
			c.cfg.Lggr.Errorw("could not deduct balance for capability request", "capReq", request.Id, "capReqCallbackID", request.CallbackId, "err", err)
		}
```

**File:** core/services/workflows/v2/capability_executor.go (L257-257)
```go
	capResp, err := capability.Execute(execCtx, capReq)
```

**File:** core/services/workflows/v2/capability_executor.go (L262-284)
```go
	c.metrics.With(platform.KeyCapabilityID, request.Id).UpdateCapabilityExecutionDurationHistogram(ctx, int64(executionDuration.Seconds()))
	if err != nil {
		if capabilityError, ok := errors.AsType[caperrors.Error](err); ok {
			if capabilityError.Origin() == caperrors.OriginUser {
				execLogger.Debugw("Capability execution failed with user error", "userErr", err)
				_ = events.EmitCapabilityFinishedEvent(ctx, loggerLabels, c.WorkflowExecutionID, request.Id, meteringRef, store.StatusCompleted, request.Method, err)
				c.metrics.With(platform.KeyCapabilityID, request.Id, platform.KeyCapabilityErrorCode, capabilityError.Code().String()).IncrementCapabilityUserErrorCounter(ctx)
				return nil, fmt.Errorf("capability execution failed with user error: %w", err)
			}

			execLogger.Debugw("Capability execution failed with system error", "systemErr", err)
			_ = events.EmitCapabilityFinishedEvent(ctx, loggerLabels, c.WorkflowExecutionID, request.Id, meteringRef, store.StatusErrored, request.Method, err)
			c.metrics.With(platform.KeyCapabilityID, request.Id, platform.KeyCapabilityErrorCode, capabilityError.Code().String()).IncrementCapabilityFailureCounter(ctx)
			c.metrics.IncrementTotalWorkflowStepErrorsCounter(ctx)
			return nil, fmt.Errorf("failed to execute capability: %w", err)
		}

		execLogger.Debugw("Capability execution failed", "err", err)
		_ = events.EmitCapabilityFinishedEvent(ctx, loggerLabels, c.WorkflowExecutionID, request.Id, meteringRef, store.StatusErrored, request.Method, err)
		c.metrics.With(platform.KeyCapabilityID, request.Id, platform.KeyCapabilityErrorCode, caperrors.Internal.String()).IncrementCapabilityFailureCounter(ctx)
		c.metrics.IncrementTotalWorkflowStepErrorsCounter(ctx)
		return nil, fmt.Errorf("failed to execute capability: %w", err)
	}
```

**File:** core/services/workflows/v2/capability_executor.go (L289-293)
```go
	if meterReport != nil {
		if err = meterReport.Settle(meteringRef, capResp.Metadata); err != nil {
			execLogger.Errorw("failed to set metering for capability request", "err", err)
		}
	}
```

**File:** core/services/workflows/metering/metering.go (L55-58)
```go
	ErrStepDeductExists      = errors.New("step deduct already exists")
	ErrNoOpenCalls           = errors.New("openConcurrentCallSlots must be greater than 0")
	ErrNoDeduct              = errors.New("must call Deduct first")
	ErrStepSpendExists       = errors.New("step spend already exists")
```

**File:** core/services/workflows/metering/metering.go (L360-378)
```go
		limit, err := r.getMaxSpendForInvocation(userSpendLimit, openConcurrentCallSlots)
		if err != nil {
			return nil, err
		}

		if !limit.Valid {
			return []capabilities.SpendLimit{}, nil
		}

		step.Deduction = limit.Decimal

		// if in metering mode, exit early without modifying local balance
		if r.meteringMode {
			return []capabilities.SpendLimit{}, nil
		}

		return r.creditToSpendingLimits(info, config, limit.Decimal), r.balance.Minus(limit.Decimal)
	}
}
```

**File:** core/services/workflows/metering/metering.go (L505-509)
```go
	// Refund the difference between what local balance had been earmarked and the actual spend
	if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
		// invariant: capability should not let spend exceed reserve
		r.lggr.Info("invariant: spend exceeded reserve")
	}
```
