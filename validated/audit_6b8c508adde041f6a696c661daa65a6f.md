### Title
Claimed instant-withdraw receipts are never cleared from per-epoch claim accounting, inflating `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` and overpaying default recovery to early claimers while leaving later claimers unpayable - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary

`claimInstantWithdrawRequest` pays a user and zeroes `instantWithdrawsRequests[_user]`, but it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`, or `pendingInstantWithdraws`-adjacent per-epoch state (which is only reduced in `collectInstantWithdrawFunds`). This is the same bug class as the external report: a "claim debt"/receipt marker is updated with an amount inconsistent with what was actually paid out — the marker retains value that no longer corresponds to any held underlying. When the pool later defaults in that epoch, `defaultPendingClaimBasis` counts already-paid receipts as claim basis and `_defaultPrefundedInstantReserve` counts already-paid-out underlying as on-hand recovery reserve, inflating `defaultRecoveryPrice` above the real reserve ratio.

### Finding Description

In `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`):

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

Only the aggregate per-user counter is cleared. The per-epoch fields set in `requestInstantWithdraw` (`instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`, lines 371-372) remain stale after a successful claim, and `pendingInstantWithdraws` is only decremented when the CDO funds the queue via `collectInstantWithdrawFunds` (line 401), not when users are paid.

If the vault defaults while `pendingInstantWithdraws != 0` in the same epoch, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — including the stale, already-paid amount — to the recovery basis (lines 644-649). Symmetrically, `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` and treats the already-transferred-out underlying as held reserve (lines 716-723). `finalizeDefaultRecovery` then sets `defaultRecoveryPrice = reserveAmount * 1e18 / totalBasis` where `reserveAmount` includes ghost funds that left the contract (lines 685-692).

Two concrete consequences:

1. `defaultRecoveryPrice` is inflated: the strategy pays defaulted receipts (normal, instant, post-default) at a higher ratio than the real reserve supports. Early claimers via `_claimDefaultedInstantWithdrawRequest` / `_claimDefaultedWithdrawRequest` receive more than their fair share; the last claimers' `_transferDefaultRecovery` transfers revert for lack of balance — permanent loss of unclaimed recovery.
2. For the already-paid user, `_claimDefaultedInstantWithdrawRequest` reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, then executes `instantWithdrawsRequests[_user] -= claimBasis` on a counter that is already 0, reverting on underflow — permanently bricking `claimInstantWithdrawRequest` for that user and blocking clearance of their stale epoch entry (lines 842-856).

Existing guards do not stop this: `_onlyIdleCDO` gates only the caller, the epoch gating in `claimWithdrawRequest` does not apply to instant claims, and nothing reconciles per-epoch instant-claim accounting at claim time.

### Impact Explanation

Any unprivileged lender (KYC-passing tranche holder) who makes an instant withdraw request in an epoch that later defaults causes the default-recovery math to overstate both the claim basis and the on-hand reserve. The result is direct theft of recovery value by early default claimers at the expense of later ones, and a permanent freeze of the remaining `defaultRecoveryReserve` once the strategy's underlying balance is exhausted (transfers revert). The overstated amount equals the sum of instant claims already paid in the defaulting epoch — bounded only by the instant-withdraw volume in that epoch.

### Likelihood Explanation

The trigger is ordinary usage: an instant withdraw request that is funded and claimed, followed by a borrower default finalized via `finalizeDefaultRecovery` in the same epoch with a non-empty `pendingInstantWithdraws` remainder (partially funded instant queue — explicitly contemplated by `_defaultPrefundedInstantReserve`). No privileged misbehavior is required; the honest borrower defaulting and the honest manager calling finalization are enough.

### Recommendation

In `claimInstantWithdrawRequest`, clear the per-epoch accounting alongside the aggregate counter: decrement `instantWithdrawClaimsByEpoch[requestEpoch]` and zero `instantWithdrawsRequestsByEpoch[_user][requestEpoch]` for each epoch whose receipt was funded and paid (this requires tracking the request epoch, or clearing the current-epoch entry when the claim is settled). Alternatively, reconcile the stale entries inside `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` so already-claimed receipts are excluded from both basis and prefunded reserve.

### Proof of Concept

Foundry fork sketch (structure; exact helpers depend on the repo's test fixtures in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: epoch running, allowInstantWithdraw = true, users A and B hold tranches.
// 1. A and B call requestInstantWithdraw via cdoEpoch; instantWithdrawsRequests,
//    instantWithdrawsRequestsByEpoch[N], instantWithdrawClaimsByEpoch[N] all set.
// 2. Manager startEpoch -> CDO partially funds strategy via collectInstantWithdrawFunds
//    (funds enough for A only). pendingInstantWithdraws = B.amount.
// 3. A calls claimInstantWithdrawRequest -> paid in full.
//    BUG: instantWithdrawsRequestsByEpoch[A][N] and instantWithdrawClaimsByEpoch[N]
//    still contain A.amount.
// 4. Borrower defaults; manager calls finalizeDefaultRecovery.
//    - defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N]
//      (includes A's ghost claim).
//    - _defaultPrefundedInstantReserve() = instantClaims - pendingInstantWithdraws
//      counts A's already-paid funds as held reserve.
//    - defaultRecoveryPrice is inflated by A.amount / actualBalance.
// 5. B calls claimInstantWithdrawRequest -> _claimDefaultedInstantWithdrawRequest
//    pays B at inflated price -> B receives more than pro-rata share.
// 6. A subsequent claimant (or A's own claim) reverts:
//    either strategy balance exhausted (others' claims fail) or
//    A's `instantWithdrawsRequests[A] -= staleBasis` underflows (A's claim bricked).

assertGt(defaultRecoveryPrice, actualReserve * 1e18 / realBasis); // inflated price
vm.expectRevert(); // late claimer cannot be paid
cdoEpoch.claimInstantWithdrawRequest();
```