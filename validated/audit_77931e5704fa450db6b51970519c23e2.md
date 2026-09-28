### Title
Stale per-epoch instant-withdraw records inflate `defaultPendingClaimBasis`, diluting default recovery for honest claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The GPAC bug is a value that slips past a bounds check (a negative `pps_id` indexing a 255-entry array), i.e. an index/key that is trusted without being validated against the state it refers to. The closest analog in `IdleCreditVault` is the per-epoch receipt index used for default recovery: `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are written on `requestInstantWithdraw` but are never cleared by `claimInstantWithdrawRequest`. When a default is finalized in the same epoch, `defaultPendingClaimBasis()` re-reads those stale records and counts already-paid receipts as outstanding defaulted claims, corrupting `defaultRecoveryPrice`.

### Finding Description
`requestInstantWithdraw` records receipt basis in three places: the aggregate `instantWithdrawsRequests[user]`, the per-epoch `instantWithdrawsRequestsByEpoch[user][currentEpoch]`, and the global per-epoch `instantWithdrawClaimsByEpoch[currentEpoch]` (lines 366-374). The funded-claim path `claimInstantWithdrawRequest` zeroes only the aggregate — it never clears the two per-epoch indexes (lines 387-391).

Later, when the CDO finalizes a borrower default, `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 644-649). `instantWithdrawClaimsByEpoch[epochNumber]` still contains receipts that were already funded and claimed at par in that epoch. Because instant withdrawals can be funded and claimed *within the same epoch* (no epoch-boundary wait like normal withdraws), an attacker can:

1. During a running epoch N: request instant withdraw of amount `A`, wait for `getInstantWithdrawFunds`/borrower funding, then `claimInstantWithdrawRequest` — receives `A` underlying. `instantWithdrawClaimsByEpoch[N]` still equals `A`.
2. Request a second instant withdraw of `B` (1 wei suffices) and leave it unclaimed, keeping `pendingInstantWithdraws != 0`.
3. Borrower defaults (a normal event, not attacker-caused) and the CDO calls `finalizeDefaultRecovery` while `epochNumber == N`. `defaultPendingClaimBasis()` returns `realBasis + A + B` instead of `realBasis + B`.
4. `defaultRecoveryPrice` is computed against the inflated basis, so every honest defaulted-epoch claimant (normal withdraws, other instant receipts) is paid `claimBasis * price / 1e18` at a lower ratio than they are entitled to.
5. The excess `defaultRecoveryReserve` corresponding to the phantom basis `A` can never be claimed — the attacker's own `_claimDefaultedInstantWithdrawRequest` reverts on the `instantWithdrawsRequests[user] -= claimBasis` underflow once their aggregate is smaller than the stale per-epoch entry (line 848). The funds are permanently stranded in the strategy.

The broken invariant is "one receipt one payout" / correct loss waterfall: the recovery ratio is priced against claims that no longer exist.

### Impact Explanation
Every already-claimed instant withdrawal in the default epoch permanently reduces `defaultRecoveryPrice`, so honest pending-withdraw and instant-receipt holders recover less underlying than the recovered amount warrants, and the corresponding share of `defaultRecoveryReserve` is stranded in the contract forever. The quantified loss equals the sum of stale `instantWithdrawClaimsByEpoch[defaultEpoch]` entries — i.e. up to the full volume of funded instant withdrawals processed in the defaulted epoch, which can be arbitrarily large (the attacker alone can cycle large funded instant withdrawals repeatedly before the default).

### Likelihood Explanation
Requires a borrower default finalized in the same epoch in which funded instant withdrawals were claimed, and a nonzero `pendingInstantWithdraws` at finalization. Defaults are rare, but the accounting defect is deterministic whenever the precondition holds, and any single user can manufacture the stale basis cheaply with a funded-then-claimed instant withdrawal plus a dust pending request. No privileged role misbehavior is needed.

### Recommendation
Clear the per-epoch instant receipt records in `claimInstantWithdrawRequest` when the claim is fully paid: delete `instantWithdrawsRequestsByEpoch[user][epochNumber]` (or track and decrement it per request epoch) and decrement `instantWithdrawClaimsByEpoch[epoch]` accordingly. Alternatively, have `defaultPendingClaimBasis()` derive the instant claim basis from `pendingInstantWithdraws`/still-outstanding aggregates rather than the cumulative per-epoch counter, so claimed receipts can never re-enter the recovery basis.

### Proof of Concept
```solidity
// Fork test on the epoch CDO + IdleCreditVault with instant withdrawals enabled.
// Assumes helpers _depositWithUser, _startEpoch, borrower funding as in IdleCreditVault.t.sol.

function testStaleInstantReceiptInflatesDefaultBasis() external {
    uint256 A = 1000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    _depositWithUser(attacker, A);
    _depositWithUser(victim,   A);
    _startEpochAndCheckPrices(0);

    uint256 epoch = strategy.epochNumber();

    // 1) attacker instant-withdraws A, CDO funds it, attacker claims at par
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(tranche.balanceOf(attacker), address(tranche));
    _fundInstantWithdraws();          // borrower/CDO funds pendingInstantWithdraws
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    // stale records still hold A
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, epoch), A);
    assertEq(strategy.instantWithdrawClaimsByEpoch(epoch), A);
    assertEq(strategy.instantWithdrawsRequests(attacker), 0);

    // 2) attacker leaves a dust instant request pending so the basis gate opens
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(1, address(tranche)); // pendingInstantWithdraws = 1

    // 3) victim has a real pending normal withdraw of V
    vm.prank(victim);
    cdoEpoch.requestWithdraw(tranche.balanceOf(victim), address(tranche));
    uint256 realBasis = strategy.pendingWithdraws() + 1;

    // 4) borrower defaults; recovery finalized in the same epoch
    _finalizeDefaultWithRecovery(/* recovered amount R < realBasis */);

    // basis used for defaultRecoveryPrice includes phantom A
    // honest price would be R / realBasis; actual is R / (realBasis + A)
    // victim's payout is reduced and ~A*price worth of reserve is stranded
    uint256 price = strategy.defaultRecoveryPrice();
    assertLt(price, R * 1e18 / realBasis);

    vm.prank(victim);
    uint256 got = cdoEpoch.claimWithdrawRequest();
    assertLt(got, /* victim fair share at honest price */);

    // stranded reserve can never be claimed (attacker's stale claim underflows)
    assertGt(strategy.defaultRecoveryReserve(), /* unclaimable dust > 0 */);
}
```