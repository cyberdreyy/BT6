### Title
Instant-withdraw claim leaves stale per-epoch receipt basis, inflating default-recovery price and freezing recovery funds — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is a reference-count leak: a resource is acquired (`of_find_node_by_name`) but never released (`of_node_put`), so the count stays elevated forever. The analog in `IdleCreditVault` is the instant-withdraw receipt counters. `requestInstantWithdraw` increments three counters per request — `instantWithdrawsRequests[user]`, `instantWithdrawsRequestsByEpoch[user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]` — but `claimInstantWithdrawRequest` only clears the first one. The per-epoch basis is never decremented on a normal funded claim, so already-paid receipts keep "refcounted" claim basis that is later reused by default-recovery accounting.

### Finding Description
In `requestInstantWithdraw`, the strategy records receipt basis at both per-user-per-epoch and per-epoch granularity: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 371–372). These aggregates exist specifically so `finalizeDefaultRecovery` can reconstruct the pending-claim basis via `defaultPendingClaimBasis`, which adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 644–648), and `_defaultPrefundedInstantReserve`, which treats `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as already-held recovery cash (lines 716–723).

However, the non-defaulted claim path `claimInstantWithdrawRequest` burns the receipt and zeroes only `instantWithdrawsRequests[_user]` (lines 387–392). It never touches `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. Only the post-default path `_claimDefaultedInstantWithdrawRequest` clears them (lines 847–853). So every instant receipt that was funded and claimed in the same epoch before a borrower default remains counted in the epoch aggregate — a leaked reference.

The concrete sequence:

1. Epoch N running. Users A and B each call `requestInstantWithdraw(100)` (KYC'd lenders, unprivileged). Now `instantWithdrawClaimsByEpoch[N] = 200`, `pendingInstantWithdraws = 200`.
2. The CDO only collects 100 via `collectInstantWithdrawFunds(100)` → `pendingInstantWithdraws = 100`.
3. A calls `claimInstantWithdrawRequest`, receives the 100 funded underlyings. Her per-epoch entries stay at 100; the epoch aggregate stays 200. This is the leak.
4. Borrower defaults; `finalizeDefaultRecovery` runs with epoch still N and `pendingInstantWithdraws = 100 != 0`.
   - `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[N] = 200` even though only B's 100 is still owed (line 647).
   - `_defaultPrefundedInstantReserve` computes `200 - 100 = 100` of phantom "already-held" reserve that was actually already paid out to A (lines 720–721).
   - `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` (line 686) counts tokens that no longer exist, so `recoveryPrice = reserveAmount / totalBasis` is computed against a denominator inflated by 100 and a numerator inflated by 100 of non-existent cash.
5. `defaultRecoveryReserve = reserveAmount` now exceeds the strategy's real token balance. B (the attacker, claiming first) is paid `claimBasis * recoveryPrice / RECOVERY_FULL` from `_transferDefaultRecovery` (lines 912–916), extracting a pro-rata share priced against phantom funds. Subsequent claimants — other defaulted normal receipt holders and active-NAV claims routed through the reserve — find `defaultRecoveryReserve` insolvent: `_transferDefaultRecovery` underflows/reverts, or `_transferFundedClaim`'s reserve guard `balance - reserve < _amount` reverts (lines 899–905).

### Impact Explanation
Direct theft plus permanent freezing of unclaimed recovery funds. The first claimant(s) after finalization withdraw at a recovery price computed on reserve that includes tokens already paid to A, draining more than their fair share; the reserve is then insolvent and later defaulted-receipt and funded-receipt claims revert permanently (there is no path to replenish `defaultRecoveryReserve`). Quantified example: with 100 actually held + 100 externally recovered against a true pending basis of 100 instant + X active, the leaked 100 of phantom basis/reserve transfers up to 100 units of real recovery to early claimants and leaves an equivalent amount permanently unclaimable by the remaining claimants.

### Likelihood Explanation
Requires: instant withdrawals enabled on the vault, an epoch where instant requests are only partially funded before epoch end (a normal occurrence — `pendingInstantWithdraws` stays non-zero precisely when funding covered only part of the queue), at least one instant claim in the same epoch, and a subsequent borrower default with recovery finalization while `epochNumber` is still that epoch. All steps use only unprivileged lender actions plus honest manager/borrower behavior; no privileged role is the attacker. The stale-accounting precondition is created automatically on every routine instant claim, so any default in an epoch with prior same-epoch instant claims triggers it.

### Recommendation
Decrement the per-epoch counters in `claimInstantWithdrawRequest` the same way `_claimDefaultedInstantWithdrawRequest` does: after computing `amount`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` proportionally and subtract it from `instantWithdrawClaimsByEpoch[epochNumber]`. Since one claim burns the whole `instantWithdrawsRequests[_user]` balance which may span multiple epochs, the clean fix is to track and clear each contributing epoch's entry (or record the request epoch, as `lastWithdrawRequest` does for normal withdrawals) so the aggregate always equals the sum of unclaimed receipts.

### Proof of Concept
```solidity
// Foundry fork test sketch against deployed IdleCDOEpochVariant + IdleCreditVault
function test_InstantClaimLeakInflatesRecovery() public {
    // setup: epoch running, unscaledApr > 0, instant withdraws allowed
    uint256 amt = 100e6; // 100 USDC each
    // A and B deposit via CDO, epoch starts, then both request instant withdraw
    cdo.requestInstantWithdraw(amt, AATranche); // as user A (tranche holder)
    cdo.requestInstantWithdraw(amt, AATranche); // as user B

    // CDO funds only half the instant queue
    vm.prank(manager);
    strategy.collectInstantWithdrawFunds(amt); // pendingInstantWithdraws: 200 -> 100

    // A claims her funded receipt inside the same epoch
    vm.prank(address(cdo));
    strategy.claimInstantWithdrawRequest(A);
    // LEAK: instantWithdrawClaimsByEpoch[epoch] still 200, A's per-epoch entry still 100
    assertEq(strategy.instantWithdrawsRequestsByEpoch(A, epoch), amt); // should be 0
    assertEq(strategy.instantWithdrawClaimsByEpoch(epoch), 2 * amt);   // should be amt

    // borrower defaults; CDO finalizes recovery with R recovered externally
    _borrowerDefault();
    cdo.finalizeDefaultRecovery(recovered, recoverySource);

    // reserve is inflated by `amt` phantom tokens
    uint256 reserve = strategy.defaultRecoveryReserve();
    uint256 realBal = underlying.balanceOf(address(strategy));
    assertGt(reserve, realBal); // accounting claims more than exists

    // B claims first and is overpaid; a later honest claimant's claim reverts
    vm.prank(address(cdo));
    strategy.claimInstantWithdrawRequest(B); // succeeds, drains real tokens
    vm.expectRevert(); // insolvent reserve -> permanent freeze
    vm.prank(address(cdo));
    strategy.claimWithdrawRequest(otherUser);
}
```