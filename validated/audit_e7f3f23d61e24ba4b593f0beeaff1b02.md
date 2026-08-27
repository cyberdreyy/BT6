## Finding

### Title
Metering balance not debited when a capability's actual spend exceeds its earmarked deduction, allowing under-billing and stale local balance during workflow execution - (File: core/services/workflows/metering/metering.go)

### Summary
`Report.Settle()` computes a "refund" for a capability step as `step.Deduction.Sub(spentCredits)` and applies it via `balanceStore.Add()`. When the actual spend (`spentCredits`, derived from capability/node responses) exceeds what was earmarked (`step.Deduction`), this delta is negative. `balanceStore.Add()` rejects any negative amount with `ErrInvalidAmount`, so the call silently no-ops and the error is only logged — the local credit balance is never reduced by the overage.

### Finding Description
In `Report.Settle` [1](#0-0) , the intended behavior is: if a capability spent less than what was deducted, refund the difference to the balance; if it spent more, the balance should be reduced by the extra amount. However `balanceStore.Add()` is hard-coded to only accept non-negative deltas: [2](#0-1) 

When `spentCredits > step.Deduction` (overspend case), `step.Deduction.Sub(spentCredits)` is negative, `Add()` returns `ErrInvalidAmount`, and the code path only logs `"invariant: spend exceeded reserve"` without ever calling `Minus` (or any other operation) to actually apply the negative correction to `bs.balance`. This is confirmed by the existing test that explicitly exercises this branch and asserts `require.NoError(t, report.Settle(...))` with only a log line emitted, not a balance change: [3](#0-2) 

This is structurally the same bug class as the referenced report: an unsigned/validated-non-negative accumulator (`overbalancedFunding`/`underbalancedFunding` as `uint256`, here `balanceStore.balance` guarded by `Add`'s `amount.LessThan(decimal.Zero)` check) is used in a context where the delta can legitimately be negative, causing the "debit" side of the calculation to be silently dropped instead of applied.

`spentCredits` is derived from `capabilities.ResponseMetadata.Metering` values returned by capability/DON node execution during a workflow run initiated by a workflow owner — a normal, unprivileged client-triggered code path, not a mocked-only or operator-only path.

### Impact Explanation
Because the overage is never subtracted:
1. **Under-billing at the report level for local-authorization purposes**: `r.balance.AddSpent(spentCredits)` still records the correct total spend for the final receipt (`SendReceipt`), but the *authorizable* `balanceStore.balance` used by subsequent `Deduct` calls (via `ByResource`/`ByDerivedAvailability`) within the same workflow execution is not reduced by the excess. This means the in-memory balance available for authorizing further capability calls in the same execution is higher than it should truly be after an overspend event, potentially allowing additional capability invocations to be deducted/executed in the same run based on a balance that doesn't reflect real consumption.
2. This directly parallels the reported bug's impact: "wrong accounting of funding fees" — here, wrong accounting of consumed credits, where a spend that should reduce available balance does not.

### Likelihood Explanation
Any capability whose reported spend (via `MeteringNodeDetail.SpendValue`) legitimately or through node variance exceeds the amount earmarked by `Deduct` will trigger this branch — this does not require a malicious node/peer, only a capability response reporting a spend value greater than what was reserved, which is a realistic operational scenario (e.g., underestimated concurrency-derived limits in `ByDerivedAvailability`, or rate/config mismatches).

### Recommendation
Replace the `balanceStore.Add(step.Deduction.Sub(spentCredits))` call with logic that handles both signs explicitly: if `spentCredits <= step.Deduction`, `Add` the refund as today; if `spentCredits > step.Deduction`, call `Minus` (or an internal unlocked equivalent) with the excess amount so the local balance is actually reduced by the true overage, even allowing it to go to zero/negative-flagged state to prevent further authorization within the same execution, rather than only logging an "invariant" message.

### Proof of Concept
1. Reserve a balance and `Deduct` a step with `step.Deduction = 1` credit (e.g., `ByResource(testUnitA, "", decimal.NewFromInt(1))`).
2. `Settle` the step with `metadata.Metering` reporting a spend of `2` units (`SpendValue: "2"`) for `testUnitA`, so `spentCredits = 2 > step.Deduction = 1`.
3. Observe (as in the existing test `metering_test.go:887-912`) that `Settle` returns `nil` error and only logs `"invariant: spend exceeded reserve"` — the local `balance.balance` is left unchanged from the overspend delta (`1 - 2 = -1`), i.e., the extra 1 credit consumed is never subtracted from the authorizable local balance, even though `AddSpent(2)` correctly records it for the final receipt.

### Citations

**File:** core/services/workflows/metering/metering.go (L505-509)
```go
	// Refund the difference between what local balance had been earmarked and the actual spend
	if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
		// invariant: capability should not let spend exceed reserve
		r.lggr.Info("invariant: spend exceeded reserve")
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
