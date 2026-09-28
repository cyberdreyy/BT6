### Title
Withdraw-request interest adjustment uses a stale `trancheAPRSplitRatio`, so AA/BB yield is misallocated when the ratio shifts between `requestWithdraw` and `stopEpoch` - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
This is the direct analog of the Convergence "cached `totalWeight` vs per-gauge weights" issue: an aggregate adjustment (`_diff` applied to `expectedEpochInterest`) is computed and locked at request time using the current `trancheAPRSplitRatio`, while the actual interest waterfall in `_updateAccounting` distributes the epoch gain using whatever `trancheAPRSplitRatio` is in effect at `stopEpoch`. If the split ratio moves between the two transactions — e.g. via fee-share minting (`_updateSplitRatio(_getAARatio(true))`) or an owner/manager `setTrancheAPRSplitRatio` call — the amount previously deducted from one tranche's expected interest no longer matches the ratio used for final distribution, so one tranche is overpaid and the other underpaid.

### Finding Description
`requestWithdraw` → `_calcInterestWithdrawRequest` computes, per withdrawal, both the user's interest and `_diff` = interest-without-split − interest-with-split, using the then-current `trancheAPRSplitRatio` via `_calcTrancheInterestShare`, and that `_diff` is folded into `expectedEpochInterest` (IdleCDOEpochVariant.sol:856-885). At `stopEpoch`, `_updateAccounting` distributes the realized gross interest across AA and BB using the *live* `trancheAPRSplitRatio` (lines 436, 883-885), and the same function later refreshes the ratio itself through `_updateSplitRatio(_getAARatio(true))` in the minted-fee path (line 448). Nothing reconciles the request-time ratio assumption with the stop-time ratio: the pending-receipt deduction is fixed while the weights are not — exactly the stale-totalWeight-vs-gaugeWeight desync. The same staleness applies to `_lastSavedNAV(_tranche)` (line 869): the per-tranche basis used to pro-rate the withdrawing user's interest is snapshotted at a different time than the NAV used at settlement.

### Impact Explanation
The residual epoch interest credited to remaining AA/BB LPs is computed with weights that differ from the weights used to compute the deductions, so yield is transferred between tranche classes. Because AA holders are the senior (loss-protected) class and are also the fee receivers, a drift that raises the AA share can permanently divert BB yield to AA/fee recipients; the inverse direction underpays AA. Loss is bounded by the per-epoch interest times the ratio delta, but it is a real, permanent misallocation of unclaimed yield rather than a rounding artifact.

### Likelihood Explanation
Requires `trancheAPRSplitRatio` (or the NAV basis feeding `_getAARatio`) to change between a user's `requestWithdraw` and the next `stopEpoch`. Deposits/withdrawals of the opposite tranche during the epoch and the fee-share minting inside `stopEpoch` both move the effective AA ratio, and the owner can update the split directly — so the desync is reachable through ordinary, honest sequencing of calls, matching the original report's "schedule actions accordingly" failure mode. I could not fully enumerate every `_updateSplitRatio` call site (e.g. inside deposit paths) within the available iterations, so the exact set of ratio-moving triggers is partially unverified; if deposits during a running epoch do not refresh the ratio, the window narrows to owner-initiated ratio changes.

### Recommendation
Snapshot the `trancheAPRSplitRatio` (and the tranche NAV basis) used for withdraw-request interest at the epoch boundary — e.g. store the ratio at `startEpoch`/`stopEpoch` and have `_calcInterestWithdrawRequest` read that snapshot rather than the live `trancheAPRSplitRatio` — or recompute pending-request `_diff` contributions at `stopEpoch` with the final ratio before `_updateAccounting`.

### Proof of Concept
Foundry fork sketch (mains/fork harness as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// epoch running, AA:BB = configured ratio R0
cdoEpoch.startEpoch();
// user requests withdraw -> _diff computed with R0, expectedEpochInterest adjusted
vm.prank(user);
cdoEpoch.requestWithdraw(amount, AATranche);
// ratio shifts before settlement (opposite-tranche deposit or owner setTrancheAPRSplitRatio -> R1)
vm.prank(otherLP);
cdoEpoch.depositAA(bigAmount); // moves _getAARatio / trancheAPRSplitRatio
vm.warp(cdoEpoch.epochEndDate() + 1);
// borrower repays, stopEpoch -> _updateAccounting distributes gain with R1
// while the pending-request deduction was computed with R0
vm.prank(manager);
cdoEpoch.stopEpoch(newApr, interest);
// invariant: sum of per-tranche credited interest + claimed withdraw interest != gross interest
assertApproxEqAbs(aaInterest + bbInterest + withdrawInterest, grossInterest, 0);
// fails: one tranche's lastNAV is higher/lower than the fair split by
// roughly |R1 - R0| * withdrawerInterest / FULL_ALLOC
```

Caveat: the PoC's triggering step assumes an in-epoch deposit (or owner call) updates `trancheAPRSplitRatio`; if verification shows the ratio can only change inside `stopEpoch` after `_updateAccounting`, the desync window collapses and this reduces to informational rather than exploitable.