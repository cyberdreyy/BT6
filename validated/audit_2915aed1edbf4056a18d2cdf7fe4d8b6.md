### Title
Funded instant-withdraw receipts claimed before default remain in `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch` and are double-counted in default recovery — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipts both in aggregate (`instantWithdrawsRequests[_user]`, `pendingInstantWithdraws`) and per-epoch (`instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`). When a user claims an already-funded instant withdrawal *before* default finalization, `claimInstantWithdrawRequest` zeroes only the aggregate counters — the per-epoch entries are never cleared. At `finalizeDefaultRecovery`, those stale per-epoch receipts are still counted in the claim basis (`defaultPendingClaimBasis`) and assumed to be held as prefunded reserve (`_defaultPrefundedInstantReserve`), even though the underlying was already paid out. This mirrors CVE-2016-4805: a channel is unregistered (receipt claimed at par) but a stale per-epoch pointer remains and is dereferenced again by a later accounting pass.

### Finding Description
Relevant code:

- `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]`, zeroes it, and pays via `_transferFundedClaim`, but never touches `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]` (lines 380–393). The per-epoch entries are only cleared inside `_claimDefaultedInstantWithdrawRequest` (lines 842–856).
- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the haircut basis whenever `pendingInstantWithdraws != 0` (lines 644–649).
- `_defaultPrefundedInstantReserve` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as underlying supposedly still held in the strategy (lines 716–723). If some of those claims were already paid out pre-default, this "reserve" does not exist.
- `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` where `reserveAmount` includes the phantom prefunded amount and `totalBasis` includes the stale receipts (lines 679–692).

Additionally, the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` entry survives, so after finalization `_claimDefaultedInstantWithdrawRequest` will try to pay the attacker *again* at `defaultRecoveryPrice`. There is a partial mitigating factor: `instantWithdrawsRequests[_user] -= claimBasis` (line 848) would underflow unless the attacker re-opens an instant request post-default so the aggregate is large enough to cover the stale per-epoch deduction — `requestInstantWithdraw` (lines 356–375) remains callable and records into the same `epochNumber` (I could not confirm whether `IdleCDOEpochVariant` gates it off post-default; if it does not, the double-claim succeeds outright).

### Impact Explanation
Two concrete impacts, both requiring only that a second user has an unfunded instant receipt in the default epoch (`pendingInstantWithdraws != 0` at finalization):

1. **Reserve dilution / insolvency**: the phantom prefunded reserve inflates `recoveryPrice` while the actual token balance is short by the already-paid amount. Legitimate default claimants (other instant requesters, defaulted normal/APR0 receipts, active tranche holders priced via `defaultBBNav`) collectively cannot be paid; the last claimants' `_transferDefaultRecovery` reverts on insufficient balance. Broken invariant: one receipt, one payout / solvency of the isolated recovery reserve.
2. **Direct double payout**: if the attacker can inflate `instantWithdrawsRequests` post-default, their stale receipt pays a second time from the reserve.

Loss is bounded by the funded instant amount claimed pre-default in the default epoch (potentially the full prefunded instant bucket).

### Likelihood Explanation
Requires the instant-withdraw feature enabled, a borrower default, and an epoch where instant claims are only partially funded (a documented state per the comment at lines 636–640). The attacker is an unprivileged instant-withdraw requester; the only privileged actions are honest manager default handling. Likelihood is moderate — gated on a default occurring while a partially funded instant queue exists in the same epoch.

### Recommendation
In `claimInstantWithdrawRequest`, also clear `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and decrement `instantWithdrawClaimsByEpoch[currentEpoch]` (tracking the epoch of each request, or clearing all epochs for the user), so funded instant claims leave no per-epoch residue. Equivalently, have `_defaultPrefundedInstantReserve`/`defaultPendingClaimBasis` count only receipts still unclaimed.

### Proof of Concept (Foundry sketch)

```solidity
function testStaleInstantReceiptInflatesRecovery() external {
    // enable instant withdrawals (manager, honest config)
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(delay, 1000, false);

    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    _depositWithUser(victim,   10_000 * ONE_SCALE, true);

    _startEpochAndCheckPrices(0);

    // both request instant withdraws in epoch N
    vm.prank(attacker); cdoEpoch.requestInstantWithdraw(0, address(AAtranche));
    vm.prank(victim);   cdoEpoch.requestInstantWithdraw(0, address(AAtranche));

    // borrower/queue funds only attacker's share: attacker claims funded instant at par
    // (collectInstantWithdrawFunds covers part, leaving pendingInstantWithdraws > 0)
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest(); // pays attacker, leaves
    // instantWithdrawsRequestsByEpoch[attacker][N] and instantWithdrawClaimsByEpoch[N] intact

    // borrower defaults; recovery R < basis
    _checkDefault();
    vm.startPrank(manager);
    IERC20Detailed(underlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // reserveAmount included instantBasis - pendingInstant that was already paid out;
    // recoveryPrice > funded ratio -> last claimants revert on insufficient balance
    vm.prank(victim);
    cdoEpoch.claimInstantWithdrawRequest(); // or reverts / overpays from phantom reserve
}
```

Uncertainty: I could not verify whether `IdleCDOEpochVariant.requestInstantWithdraw` reverts after default/finalization (needed for the full double-spend variant), nor whether `epochNumber` is bumped post-default. The reserve-dilution/insolvency half of the finding stands regardless, since it depends only on the phantom `_defaultPrefundedInstantReserve` term, which is confirmed by the code at lines 716–723.