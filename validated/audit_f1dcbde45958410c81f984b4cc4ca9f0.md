## Title
`Report.Settle()` in workflow metering only refunds surplus credits, never debits an overspend, causing phantom credit balance that bypasses the execution's billing reservation - ([File: core/services/workflows/metering/metering.go])

### Summary
`core/services/workflows/metering/metering.go`'s `Report.Settle()` pre-earmarks a spend limit for a capability step via `Deduct()` and then, once the actual node-reported spend is known, is supposed to true-up the in-memory `balanceStore`. The true-up is implemented as a single `Add()` call with `step.Deduction.Sub(spentCredits)`, which only succeeds (and only ever credits the balance back) when the actual spend is *less than* the earmarked amount. When actual spend *exceeds* the earmarked amount, the negative delta is rejected by `balanceStore.Add()` and the shortfall is silently dropped after only being logged — the balance is never debited for the overage. This is the same asymmetric-correction bug class as the reported Autonolas `effectiveBond` issue: a pre-committed capacity value is corrected in only one direction.

### Finding Description
`Deduct()` earmarks credits from the workflow's local `balanceStore` before a capability step executes (e.g. `ByResource`/`ByDerivedAvailability` call `r.balance.Minus(...)`) [1](#0-0) [2](#0-1) .

After the step executes, `Settle()` aggregates the node-reported `SpendValue`s into `spentCredits` and attempts to true-up the balance:
```go
// Refund the difference between what local balance had been earmarked and the actual spend
if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
    // invariant: capability should not let spend exceed reserve
    r.lggr.Info("invariant: spend exceeded reserve")
}
r.balance.AddSpent(spentCredits)
``` [3](#0-2) 

`balanceStore.Add()` explicitly rejects any negative argument:
```go
func (bs *balanceStore) Add(amount decimal.Decimal) error {
    ...
    if amount.LessThan(decimal.Zero) {
        return ErrInvalidAmount
    }
    bs.balance = bs.balance.Add(amount)
    return nil
}
``` [4](#0-3) 

So when `spentCredits > step.Deduction` (actual usage exceeds the earmarked/predicted spend for that step — analogous to the settled epoch's `maxBond` exceeding what the pre-credit assumed, just mirrored), `Add()` returns `ErrInvalidAmount`, the code only logs a message, and `bs.balance` is **not** reduced by the overage. The balance retains phantom credit exactly equal to the overspend amount that was never charged against it. This is confirmed by the test explicitly documenting that "spent credits are disconnected from balance updates" and that overspend does not affect the balance: [5](#0-4) , and by the dedicated test "does not error when spend exceeds reservation" which asserts only that a log line is produced, not that the balance is corrected: [6](#0-5) .

By contrast, `bs.spent` (used later for the `CreditsConsumed` field reported to the billing service in `SendReceipt`) is always incremented by `AddSpent(spentCredits)` regardless of whether the balance debit succeeded [7](#0-6) [8](#0-7) . This means the *post-hoc billing receipt* is accurate, but the *live in-memory balance* — which is the sole gatekeeper for subsequent `Deduct()` calls within the same workflow execution via `getMaxSpendForInvocation`/`ByDerivedAvailability` — is never reduced to reflect the true overage.

### Impact Explanation
Within a single workflow execution, once one step's actual (node-reported) spend legitimately exceeds its earmarked deduction (e.g., due to gas price/compute variance, no malicious actor required), the local `balanceStore.balance` retains phantom credit equal to the shortfall. Later `Deduct()` calls in the same execution (`ByResource`, `ByDerivedAvailability`) are gated purely by this local balance, so the workflow can authorize additional capability spend beyond what its `ReserveCredits` reservation from the billing service actually covers. Real DON capability resources (compute, gas-funded actions, etc.) can therefore be consumed in excess of the reserved credit envelope for the remainder of that execution, even though the final `SubmitWorkflowReceipt` call still reports the true total consumed. This is a live quota-bypass within the execution, not merely a bookkeeping discrepancy corrected later.

### Likelihood Explanation
This does not require a malicious node: `spentCredits` is a median-aggregated value across DON nodes (`medianSpend`), so it is resistant to a single dishonest node, but any workflow whose real resource usage for a step naturally exceeds its predicted/derived spend limit (a plausible, non-adversarial scenario, especially for `ByDerivedAvailability` steps where the limit is heuristically derived rather than hard-capped) will trigger this path every time. The only defense present is a log line (`r.lggr.Info("invariant: spend exceeded reserve")`), confirming the developers recognized the scenario but did not implement the corrective branch.

### Recommendation
In `Settle()`, when `spentCredits > step.Deduction`, explicitly debit the overage from the balance (e.g., via `balanceStore.Minus`, clamping at zero or allowing it to go negative consistent with metering-mode semantics) instead of silently dropping the correction:
```go
diff := step.Deduction.Sub(spentCredits)
if diff.IsNegative() {
    if err := r.balance.Minus(diff.Neg()); err != nil {
        r.lggr.Infow("invariant: spend exceeded reserve", "err", err)
    }
} else if err := r.balance.Add(diff); err != nil {
    r.lggr.Info("invariant: spend exceeded reserve")
}
```

### Proof of Concept
1. Reserve a workflow execution with `ReserveCredits` returning a small credit balance.
2. Call `Deduct("step1", ByDerivedAvailability(...))` earmarking `N` credits.
3. Call `Settle("step1", metadata)` where the aggregated `spentCredits` (from `metadata.Metering`) is greater than `N` (e.g., node reports higher `SpendValue` than predicted).
4. Observe `r.balance.Add(...)` returns `ErrInvalidAmount`, only a log line is emitted, and `report.balance.balance` is unchanged instead of being reduced by the overage — matching the assertion pattern seen in `Test_Report_Settle`'s "does not error when spend exceeds reservation" test [6](#0-5) .
5. Call `Deduct("step2", ...)` again — it succeeds against the inflated balance, allowing the workflow to authorize more total capability spend than its original `ReserveCredits` reservation.

### Citations

**File:** core/services/workflows/metering/metering.go (L330-338)
```go
		step.Deduction = bal

		// if in metering mode, exit early without modifying local balance
		if r.meteringMode {
			return []capabilities.SpendLimit{}, nil
		}

		return []capabilities.SpendLimit{}, r.balance.Minus(bal)
	}
```

**File:** core/services/workflows/metering/metering.go (L373-377)
```go
			return []capabilities.SpendLimit{}, nil
		}

		return r.creditToSpendingLimits(info, config, limit.Decimal), r.balance.Minus(limit.Decimal)
	}
```

**File:** core/services/workflows/metering/metering.go (L505-511)
```go
	// Refund the difference between what local balance had been earmarked and the actual spend
	if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
		// invariant: capability should not let spend exceed reserve
		r.lggr.Info("invariant: spend exceeded reserve")
	}

	r.balance.AddSpent(spentCredits)
```

**File:** core/services/workflows/metering/metering.go (L636-644)
```go
	req := billing.SubmitWorkflowReceiptRequest{
		WorkflowOwner:                 r.labels[platform.KeyWorkflowOwner],
		WorkflowId:                    r.labels[platform.KeyWorkflowID],
		WorkflowExecutionId:           r.labels[platform.KeyWorkflowExecutionID],
		WorkflowRegistryAddress:       r.workflowRegistryAddress,
		WorkflowRegistryChainSelector: r.workflowRegistryChainSelector,
		Metering:                      r.FormatReport(),
		CreditsConsumed:               r.balance.GetSpent().String(),
	}
```

**File:** core/services/workflows/metering/balance_store.go (L171-183)
```go
// Add increases the current credit balance.
func (bs *balanceStore) Add(amount decimal.Decimal) error {
	bs.mu.Lock()
	defer bs.mu.Unlock()

	if amount.LessThan(decimal.Zero) {
		return ErrInvalidAmount
	}

	bs.balance = bs.balance.Add(amount)

	return nil
}
```

**File:** core/services/workflows/metering/balance_store.go (L204-209)
```go
func (bs *balanceStore) AddSpent(amount decimal.Decimal) {
	bs.mu.Lock()
	defer bs.mu.Unlock()

	bs.spent = bs.spent.Add(amount)
}
```

**File:** core/services/workflows/metering/balance_store_test.go (L114-131)
```go
	t.Run("spent credits are disconnected from balance updates", func(t *testing.T) {
		t.Parallel()

		// Start with 10 credits, spend 5, then add back 8 (more than was spent)
		balanceStore, err := NewBalanceStore(ten, map[string]decimal.Decimal{"resourceA": decimal.NewFromInt(1)})
		require.NoError(t, err)

		// Spend 5 credits
		require.NoError(t, balanceStore.Minus(five))
		assert.True(t, balanceStore.GetSpent().Equal(decimal.Zero), "spent amount should not be updated")

		// Add back 8 credits (more than was spent) - spent should not go negative
		require.NoError(t, balanceStore.Add(eight))
		assert.True(t, balanceStore.GetSpent().Equal(decimal.Zero), "spent amount should not be updated")

		balanceStore.AddSpent(five)
		assert.True(t, balanceStore.GetSpent().Equal(five), "spent amount should reflect actual capability spend")
	})
```

**File:** core/services/workflows/metering/metering_test.go (L887-912)
```go
	t.Run("does not error when spend exceeds reservation", func(t *testing.T) {
		t.Parallel()

		billingClient := mocks.NewBillingClient(t)
		lggr, logs := logger.TestObserved(t, zapcore.InfoLevel)
		billingClient.EXPECT().GetWorkflowExecutionRates(mock.Anything, mock.Anything).
			Return(&billing.GetWorkflowExecutionRatesResponse{
				RateCards: successRates,
			}, nil)
		report := newTestReport(t, lggr, billingClient)

		billingClient.EXPECT().ReserveCredits(mock.Anything, mock.Anything).
			Return(&successReserveResponseWithRates, nil)
		require.NoError(t, report.Reserve(t.Context()))

		steps := capabilities.ResponseMetadata{Metering: []capabilities.MeteringNodeDetail{
			{Peer2PeerID: "xyz", SpendUnit: testUnitA, SpendValue: "2"},
		}}

		_, err := report.Deduct("ref1", ByResource(testUnitA, "", decimal.NewFromInt(1)))
		require.NoError(t, err)

		require.NoError(t, report.Settle("ref1", steps))
		assert.Len(t, logs.All(), 1)
		billingClient.AssertExpectations(t)
	})
```
