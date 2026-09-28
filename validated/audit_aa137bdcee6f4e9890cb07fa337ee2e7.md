### Title
Unfunded APR0 interest is committed to `pendingWithdraws`/`apr0RateByEpoch` before the borrower-default early return in `_stopEpoch` — ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The kernel bug is a classic resource leak on an error path: `mpol_dup()` allocates a policy, then an error return skips `mpol_put()`. The direct analog in this codebase is in `IdleCDOEpochVariant._stopEpoch`: the strategy call `prepareStopEpochWithApr0` **commits** epoch-accounting state (it is a mutating call, not a view) before the function knows whether the borrower will pay. When the borrower subsequently defaults — via `onStopEpoch` returning `false` (programmable mode) or `getFundsFromBorrower` reverting — the function takes the default early return, and the committed APR0 accrual is never rolled back. The strategy is left carrying an unfunded liability that enters the default-recovery claim basis.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol:361-404`, `_stopEpoch` executes:

1. `(_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest)` — this call mutates `IdleCreditVault` state: when `apr0TotalPrincipal != 0` and realized interest exists, it does `pendingWithdraws += _apr0NetInterest`, stores `apr0RateByEpoch[epochNumber]`, and closes the bucket with `apr0TotalPrincipal = 0` (`contracts/strategies/idle/IdleCreditVault.sol:533-540`).
2. Only afterwards does the CDO check whether funds actually arrive: `onStopEpoch(...) == false` → `_handleBorrowerDefault` + `return` (line 398-402), or `getFundsFromBorrower` throwing → `catch` → `_handleBorrowerDefault` (line 501-504).

On the default path, `expectedEpochInterest`/`pendingWithdrawFees` persistence is intended, but the APR0 commit is not matched by any funding: the borrower never paid `_apr0NetInterest`, yet it remains in `pendingWithdraws`. Default finalization computes the recovery basis over `activeBasis + pendingWithdraws` (the same total-basis formula used by `previewLossAdjustedWithdrawFunds` at `IdleCreditVault.sol:454-457` and exercised in `IdleCDOEpochQueue.t.sol:817-822`). Per-user APR0 receipts keep `apr0Users[user].principal` open and accrue interest via `apr0RateByEpoch` in `_settleApr0` / `_withdrawClaimAmountsForEpoch` (`IdleCreditVault.sol:551-560, 862-870`).

The broken invariant is solvency of the recovery pool: a claim for interest that was never paid is permanently recorded before the error branch, exactly like the leaked `mpol_dup()` allocation. Recovery is a fixed-size pool (the actually recovered underlying), so the phantom APR0 interest both inflates the claim basis denominator and mints a corresponding claim for APR0 requesters.

### Impact Explanation
Any APR0-mode epoch that ends in borrower default leaks value equal to `_apr0NetInterest` from the recovery pool. APR0 withdraw requesters are paid recovery on interest that was never funded, diluting `defaultRecoveryPrice`/`lossRecoveryPrice` for every other claimant — active AA/BB tranche holders and normal withdraw requesters lose a pro-rata share of real recovered funds. The effect is irreversible: `apr0TotalPrincipal` is zeroed so the bucket cannot be re-accrued or corrected, and `apr0RateByEpoch[epochNumber]` is immutable once stored.

### Likelihood Explanation
Requires an APR0-mode vault (`unscaledApr == 0`, `apr0TotalPrincipal > 0`), a `stopEpoch`/`stopEpochWithDuration` call with nonzero interest, and a borrower default on that stop — all reachable by the honest manager sequencing plus an undercollateralized borrower, which is an in-scope scenario rather than attacker-controlled. The attacker angle is an APR0 withdraw requester (tranche-token holder) who requests withdraw during the APR0 window and then claims recovery on unfunded interest after the default finalizes. The conditions are narrow (APR0 mode + same-epoch default), but when they occur the leak is deterministic and cannot be cleaned up by any later call.

### Recommendation
Move `prepareStopEpochWithApr0` inside the funded branch (after `getFundsFromBorrower` succeeds), or snapshot and restore the mutated fields (`pendingWithdraws`, `apr0RateByEpoch[epochNumber]`, `apr0TotalPrincipal`) in both default branches — before `_handleBorrowerDefault` at line 401 and inside the `catch` at line 501. Alternatively, have `_handleBorrowerDefault`/`finalizeDefaultRecovery` explicitly exclude unfunded APR0 accrual recorded in the defaulting epoch from the recovery basis.

### Proof of Concept
Reproducible Foundry fork PoC sketch (modeled on `test/foundry/IdleCDOEpochQueue.t.sol:811-858`):

```solidity
// Setup: APR0 pool (unscaledApr == 0), APR0 user requests withdraw so
// apr0TotalPrincipal > 0. Epoch runs with expected interest > 0.
// 1. manager calls stopEpochWithDuration(newApr, interest, duration, 0)
//    but borrower repays nothing (programmable: onStopEpoch returns false,
//    or borrower simply does not approve -> getFundsFromBorrower reverts).
// 2. Assert leaked state persisted despite default:
assertEq(strategy.apr0TotalPrincipal(), 0);                // bucket closed
assertGt(strategy.apr0RateByEpoch(strategy.epochNumber()), 0); // unfunded rate stored
assertEq(strategy.pendingWithdraws(), apr0NetInterest);    // liability without funding
assertTrue(cdoEpoch.defaulted());
// 3. Finalize default with a fixed recovery amount R.
// 4. Show APR0 user's claimBasis includes apr0 interest never paid,
//    and defaultRecoveryPrice is lower than the same scenario
//    where apr0NetInterest was rolled back:
//    dilution == apr0NetInterest * recoveredFunds / totalBasis.
```