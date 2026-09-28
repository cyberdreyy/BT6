### Title
Unfunded instant-withdraw receipts from prior epochs are excluded from default recovery basis and permanently frozen - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to CVE-2018-6340 (an out-of-bounds/stale-index read driven by attacker-influenced state), `IdleCreditVault` reads only the *current* epoch's instant-withdraw claim bucket (`instantWithdrawClaimsByEpoch[epochNumber]`) when computing the default-recovery claim basis, while `pendingInstantWithdraws` is a cross-epoch aggregate. Instant receipts requested in earlier epochs that were never fully funded contribute to `pendingInstantWithdraws` but are invisible to `defaultPendingClaimBasis()` and to `_defaultPrefundedInstantReserve()`. After `finalizeDefaultRecovery`, those receipts are also unclaimable: `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[user][defaultEpoch]`, and the par-claim fallback in `claimInstantWithdrawRequest` reverts inside `_transferFundedClaim` because all strategy balance is locked as `defaultRecoveryReserve`. Additionally, the understated `totalBasis` inflates `defaultRecoveryPrice`, so early claimants drain the reserve and later claimants' `_transferDefaultRecovery` underflows.

### Finding Description
- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` only when `pendingInstantWithdraws != 0`, i.e. only the *current* epoch's receipts. Receipts recorded under earlier epoch keys are omitted even though `pendingInstantWithdraws` (the flag that triggers inclusion) still counts them (`IdleCreditVault.sol:644-649`).
- `requestInstantWithdraw` records basis under `instantWithdrawsRequestsByEpoch[user][epochNumber]` / `instantWithdrawClaimsByEpoch[epochNumber]` (`IdleCreditVault.sol:367-372`), and `collectInstantWithdrawFunds` may fund only partially, leaving `pendingInstantWithdraws > 0` across an `epochNumber` bump (`IdleCreditVault.sol:398-403`, `deposit` increments `epochNumber` at `IdleCreditVault.sol:607-614`).
- `_defaultPrefundedInstantReserve()` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`; with old-epoch claims present this either returns 0 or misattributes prefunded cash, so `reserveAmount` in `finalizeDefaultRecovery` does not isolate funding for the omitted claims (`IdleCreditVault.sol:716-723`, `685-692`).
- Post-finalization, `_claimDefaultedInstantWithdrawRequest` only clears the `defaultRecoveryEpoch` slice; the user's residual `instantWithdrawsRequests` then falls through to the par path, where `_transferFundedClaim` reverts if paying it would dip into `defaultRecoveryReserve` (`IdleCreditVault.sol:842-856`, `380-393`, `897-906`).

Broken invariant: one receipt, one recovery-priced payout. Old-epoch receipts get neither the recovery haircut payout nor a par payout — a permanent freeze — while the deflated `totalBasis` produces a `recoveryPrice > fair`, overpaying default-epoch claimants and making the reserve insolvent for the tail of the queue.

### Impact Explanation
- Old-epoch instant receipt holders permanently lose their recovery share (their claim reverts forever; `defaultRecoveryReserve` cannot be spent via `_transferFundedClaim`).
- Because `totalBasis` is understated, `recoveryPrice` is inflated; claimants that are included receive more than fair value and the reserve is exhausted early, so later defaulted claims revert on `defaultRecoveryReserve -= _amount` — theft/freezing of unclaimed recovery proportional to the omitted basis.
- Quantified example: 100k unfunded instant basis in epoch N-1 omitted, 100k default-epoch basis included, real reserve 50k → computed price 0.5 on basis 100k instead of ~0.25 on 200k; the first default-epoch claimant doubles their payout and roughly half of all legitimate claims become unpayable.

### Likelihood Explanation
Requires: (a) an instant withdraw that is only partially funded via `collectInstantWithdrawFunds`, leaving `pendingInstantWithdraws > 0` across at least one `stopEpoch`/`epochNumber` increment, and (b) a subsequent borrower default finalized via `finalizeDefaultRecovery`. Both are normal, unprivileged-sequencable protocol states — an attacker only needs to be the user holding a stale-epoch receipt (to suffer the freeze) or an early post-default claimant (to receive the inflated payout). No privileged misbehavior needed; honest manager/borrower calls merely leave an unfunded instant remainder, which is an expected mode of the instant-withdraw queue.

### Recommendation
- Make `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` account for *all* outstanding instant receipt basis, not just `instantWithdrawClaimsByEpoch[epochNumber]` (e.g. track a global `instantWithdrawClaimsTotal`, or iterate/accumulate across epochs).
- In `_claimDefaultedInstantWithdrawRequest`, clear the user's entire unfunded instant basis (all epochs) at `defaultRecoveryPrice`, not only the `defaultRecoveryEpoch` slice.
- Alternatively, prevent new epochs from starting while `pendingInstantWithdraws` is non-zero, or roll over old receipts' basis into the new epoch key.

### Proof of Concept
Foundry fork PoC outline (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: vault running, underlying = USDC mock, users alice (old epoch), bob (new epoch)
// 1) Epoch N running: alice requests instant withdraw of 100_000e6
vm.prank(alice); cdoEpoch.requestInstantWithdraw(100_000e6, AAtranche);
// 2) stopEpoch: borrower/CDO funds only 40_000e6 via collectInstantWithdrawFunds
//    -> pendingInstantWithdraws = 60_000e6, claims keyed to epoch N
// 3) Epoch N+1: bob requests instant withdraw 100_000e6 -> keyed to epoch N+1
// 4) borrower defaults; manager finalizes:
cdoEpoch.finalizeDefaultRecovery(recovered, source); // or CDO-level finalizeDefault path
// Assertions:
// - strategy.defaultPendingClaimBasis() before finalize omitted alice's 60k basis
// - vm.prank(alice); cdoEpoch.claimInstantWithdrawRequest() -> reverts (NotAllowed
//   from _transferFundedClaim) => permanent freeze of alice's recovery share
// - recoveryPrice = reserve / understatedBasis is inflated; a pending normal
//   withdraw claimant later reverts on defaultRecoveryReserve underflow.
```

Key assertion is `assertGt(strategy.defaultRecoveryPrice(), fairPrice)` plus `vm.expectRevert` on `claimInstantWithdrawRequest` for the stale-epoch receipt and on the last defaulted-epoch claim.