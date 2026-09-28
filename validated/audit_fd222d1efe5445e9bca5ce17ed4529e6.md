### Title
Stale-epoch instant-withdraw receipts escape the default haircut and drain the recovery reserve at par — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` both index instant-withdraw receipts by a single "latest" epoch marker (`epochNumber` / `defaultRecoveryEpoch`). Unfunded instant receipts recorded in an *earlier* epoch survive in `pendingInstantWithdraws` and `instantWithdrawsRequests`, but are omitted from the recovery basis and never cleared. After `finalizeDefaultRecovery`, the holder of a stale-epoch receipt is paid 1:1 via `_transferFundedClaim`, spending the haircutted `defaultRecoveryReserve` — the same "global latest timestamp misses earlier data" bug class as the oracle report.

### Finding Description
`requestInstantWithdraw` records receipts per epoch: `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (IdleCreditVault.sol L367-374), while `epochNumber` is only bumped in `deposit()` when a new epoch starts (L607-610). If the instant queue is only partially funded, `pendingInstantWithdraws` stays non-zero across the epoch boundary — the request's basis stays keyed to the *old* epoch.

At default finalization, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch (L644-649). A stale-epoch claim contributes nothing to `totalBasis`, so `recoveryPrice` is computed against an understated denominator. Worse, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (L842-856): for the stale-epoch holder it returns 0 without touching `instantWithdrawsRequests[_user]`. Execution then falls through in `claimInstantWithdrawRequest` (L380-392), which burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim` at par. `_transferFundedClaim` spends `defaultRecoveryReserve` first (L897+), so the stale receipt is paid *unhaircutted* out of the reserve that was sized only for the haircutted basis — direct overpayment at the expense of every other recovery claimant.

This mirrors the report precisely: the "latest" lookup (current epoch key) misses a valid claim recorded at a previous tick, and behaves inconsistently with the per-epoch accounting that correctly tracked it.

### Impact Explanation
Any lender whose instant-withdraw request was left partially unfunded across an epoch boundary and then survives to a default can claim their full receipt at par from the recovery reserve instead of at `defaultRecoveryPrice`. Quantified loss: `receiptAmount * (1 - defaultRecoveryPrice)` is stolen from the reserve per stale receipt, and if aggregate stale claims exceed the slack, the reserve is drained and later legitimate claimants revert on transfer — insolvency/permanent freezing of unclaimed recovery funds.

### Likelihood Explanation
Requires: (1) an instant withdraw request left partially unfunded so `pendingInstantWithdraws > 0` crosses an epoch boundary (the test suite itself demonstrates this state at IdleCreditVault.t.sol L4515-4524, where `pendingInstant` remains >0 after `startEpoch(2)`); (2) a subsequent borrower default finalized via `finalizeDefault`. Both are reachable by unprivileged users sequencing around honest manager/borrower calls; the attacker only needs to have requested an instant withdraw and then call `claimInstantWithdrawRequest` post-finalization.

### Recommendation
In `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest`, do not rely on the single `epochNumber`/`defaultRecoveryEpoch` key. Aggregate all outstanding instant claim basis (e.g., track a global `totalInstantClaimBasis` alongside per-epoch entries, or settle/carry stale-epoch receipts into the current epoch at `deposit()` when `epochNumber` increments). Similarly, `claimInstantWithdrawRequest` should clear *all* epochs' receipts for the user at the recovery price, not only `defaultRecoveryEpoch`.

### Proof of Concept
Foundry fork PoC outline (mirroring `testMixedFundedAndUnfundedInstantClaimsStayFrozen` in test/foundry/IdleCreditVault.t.sol):

```solidity
// Epoch N: user requests instant withdraw; borrower only partially funds it.
vm.prank(user);
cdoEpoch.requestInstantWithdraw(amount);   // instantWithdrawClaimsByEpoch[N] += amount
// partial funding -> pendingInstantWithdraws > 0

// Epoch N+1 starts: epochNumber incremented, receipt still keyed under N.
_startEpochAndCheckPrices(N+1);
assertGt(creditVault.pendingInstantWithdraws(), 0);
assertEq(creditVault.instantWithdrawClaimsByEpoch(creditVault.epochNumber()), 0); // stale key

// Borrower defaults; manager finalizes recovery.
// defaultPendingClaimBasis() misses user's stale instant basis -> inflated defaultRecoveryPrice,
// and reserve does not account user's par payout.
cdoEpoch.finalizeDefault(recovered, manager);

// User claims: _claimDefaultedInstantWithdrawRequest finds 0 at defaultRecoveryEpoch,
// then instantWithdrawsRequests[user] is paid at par from defaultRecoveryReserve.
uint256 balPre = underlying.balanceOf(user);
vm.prank(user);
cdoEpoch.claimInstantWithdrawRequest();
// assert: user received `amount` (par) instead of `amount * defaultRecoveryPrice / 1e18`,
// and defaultRecoveryReserve is under-collateralized for remaining claimants.
```