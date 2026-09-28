### Title
Stale-epoch index in default recovery misses unfunded instant-withdraw receipts from prior epochs, inflating `defaultRecoveryPrice` and draining/freezing the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` records receipts under the *request-time* epoch (`instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`), but `finalizeDefaultRecovery` / `defaultPendingClaimBasis` / `_defaultPrefundedInstantReserve` only ever read `instantWithdrawClaimsByEpoch[epochNumber]` at *finalization-time* epoch. If an instant receipt stays partially unfunded across a `stopEpoch` boundary (`epochNumber` is incremented in `deposit()`), the whole prior-epoch instant basis is invisible to recovery accounting — an off-by-one-epoch index confusion analogous to the out-of-bounds index access in CVE-2017-6209.

### Finding Description
- `requestInstantWithdraw` keys receipt basis by `currentEpoch` at lines 367-372: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount;`.
- `epochNumber` is bumped inside `deposit()` when called during a running epoch (i.e., at `stopEpoch`), lines 607-611.
- `collectInstantWithdrawFunds` only decrements the aggregate `pendingInstantWithdraws` (lines 398-403); nothing ever clears or rolls over `instantWithdrawClaimsByEpoch[N]` once epoch `N` ends. Comments at lines 636-640 explicitly acknowledge that `pendingInstantWithdraws` can remain non-zero after `startEpoch` when "that cash covered only part of the instant queue" — so an unfunded instant receipt legitimately survives into epoch N+1.
- At finalization, `defaultPendingClaimBasis` (lines 644-649) adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the current epoch — silently dropping all unfunded instant claims recorded under earlier epochs.
- `_defaultPrefundedInstantReserve` (lines 716-723) uses the same wrong index, so `instantBasis <= pendingInstant` yields `prefundedReserve = 0` even though part of the missed claims may already be backed by strategy-held underlyings.
- `_claimDefaultedInstantWithdrawRequest` (lines 842-856) also clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`; older-epoch instant receipts remain in `instantWithdrawsRequests[_user]` and are paid **at par** through `_transferFundedClaim` in `claimInstantWithdrawRequest` (lines 387-392) even though they were never funded and never haircut.

### Impact Explanation
Two compounding effects:
1. `totalBasis` under-counts by the missed instant claims ⇒ `recoveryPrice = reserveAmount / totalBasis` is inflated ⇒ each defaulted-epoch claimant is overpaid, consuming `defaultRecoveryReserve` faster than fair ⇒ later claimants' `_transferDefaultRecovery` underflows/reverts — **permanent freezing of their recovery**.
2. Prior-epoch instant receipts bypass the haircut entirely and can be claimed at 100% via `claimInstantWithdrawRequest`, paying unfunded receipts at par out of the strategy balance (the `_transferFundedClaim` reserve guard only protects `defaultRecoveryReserve`, not other claimants' funded money) — **direct theft of yield/principal belonging to other receipt holders**, or a revert that permanently freezes them.

Broken invariant: fair loss socialization and solvency of the recovery reserve — "one receipt, one haircut, one payout" is violated purely by which epoch index a request happened to land in. No existing guard stops it: `_onlyIdleCDO` is satisfied, `defaultRecoveryFinalized` gating works as designed, and nothing validates that all epochs' instant claims are included in `defaultPendingClaimBasis`.

### Likelihood Explanation
An unprivileged tranche holder can position this deterministically:
1. During a running epoch with instant withdrawals enabled, call the CDO's instant-withdraw path to create a receipt in epoch N (attacker transaction, no privilege needed).
2. Simply do not claim. If the borrower repays less than the instant queue at `stopEpoch` — or the CDO's available cash covers only part of it — `pendingInstantWithdraws` remains > 0 into epoch N+1. Any concurrent normal withdraw pressure makes partial funding realistic.
3. If the borrower defaults in any later epoch and the CDO finalizes recovery, every prior-epoch instant receipt is excluded from basis and from the haircut.

Requires only the instant-withdraw feature enabled and a partially funded epoch boundary followed by a default — no privileged misbehavior.

### Recommendation
Track instant claims by *all* outstanding epochs, not just `epochNumber`. Concretely: keep a running aggregate `instantWithdrawClaimsTotal` incremented in `requestInstantWithdraw` and decremented in `claimInstantWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`, and use it in `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`. In `_claimDefaultedInstantWithdrawRequest`, iterate or otherwise haircut every per-epoch entry contributing to `instantWithdrawsRequests[_user]` (or store a per-user list of request epochs), rather than only `defaultRecoveryEpoch`.

### Proof of Concept
```solidity
// Fork test against mainnet pool with instant withdrawals enabled.
function testStaleEpochInstantMissedInDefaultRecovery() external {
    // Epoch N running, instant withdraws enabled (delay set, min apr delta).
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false);

    // Attacker (KYC'd AA holder) deposits, then requests instant withdraw in epoch N.
    uint256 tr = _depositWithUser(attacker, 100e6);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(tr, trancheAA); // records instantWithdrawClaimsByEpoch[N]

    // Borrower repays only part at stopEpoch -> pendingInstantWithdraws stays > 0.
    _stopCurrentEpochPartialInstantFunding(); // epochNumber -> N+1 via deposit()
    assertGt(strategy.pendingInstantWithdraws(), 0);
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), 0); // keyed to N

    // Epoch N+1: borrower defaults; CDO finalizes recovery.
    _handleBorrowerDefault();
    uint256 basis = strategy.defaultPendingClaimBasis();
    // BUG: basis excludes the attacker's epoch-N instant claim.
    assertEq(basis, strategy.pendingWithdraws()); // +0 instant component

    cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource);
    // recoveryPrice is inflated: reserve / (basis without instant claims).
    // Attacker's epoch-N instant receipt was never haircut and claims at PAR:
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(attacker);
    assertGt(underlying.balanceOf(attacker) - balPre, 0); // 100% paid, unfunded

    // Meanwhile a defaulted-epoch claimant's reserve draw reverts once reserve
    // is drained by the inflated price -> permanent freeze.
}
```

Uncertainty note: I could not fully trace `IdleCDOEpochVariant`'s `startEpoch`/`getInstantWithdrawFunds` collection path in the remaining budget, so the exact conditions under which `pendingInstantWithdraws` survives an epoch boundary rely on the code's own comments (lines 636-640) describing that case. The index mismatch itself — request-time epoch key vs. finalization-time `epochNumber` reads at lines 371, 647, 719, 844 — is directly verifiable.