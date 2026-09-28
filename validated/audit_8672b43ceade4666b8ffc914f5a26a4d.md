### Title
`claimInstantWithdrawRequest` clears the aggregate receipt but leaves per-epoch instant-withdraw counters stale, corrupting default-recovery basis and reserve accounting - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug pattern is "a scalar value is mutated in place while a second variable still shares/ties to the old value; the tie must be explicitly broken." In `IdleCreditVault`, instant-withdraw receipts are tracked in two tied places: the aggregate `instantWithdrawsRequests[_user]` and the per-epoch pair `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`. `claimInstantWithdrawRequest` resets only the aggregate (`instantWithdrawsRequests[_user] = 0`) and never clears the per-epoch entries. The defaulted-epoch path (`_claimDefaultedInstantWithdrawRequest`) does reset both — proving the intent that the per-epoch record tracks *unclaimed* receipts — but the normal funded path leaves the stale tie in place.

### Finding Description
In `claimInstantWithdrawRequest` (lines 380-393) the strategy burns the receipt tokens and zeroes `instantWithdrawsRequests[_user]`, but:

- `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` stays nonzero.
- `instantWithdrawClaimsByEpoch[currentEpoch]` stays nonzero.

Both mappings are later consumed by default finalization:

- `defaultPendingClaimBasis()` (line 644-649) adds `instantWithdrawClaimsByEpoch[epochNumber]` to the pending basis whenever `pendingInstantWithdraws != 0`.
- `_defaultPrefundedInstantReserve()` (lines 716-723) computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` and counts that as already-held recovery reserve.
- `_claimDefaultedInstantWithdrawRequest` (lines 842-856) decrements `instantWithdrawClaimsByEpoch[defaultEpoch]`, showing the mapping is meant to represent only *outstanding* claims.

So an instant receipt that was already fully paid keeps polluting both the claim basis and the "prefunded reserve" of its epoch — the exact analog of the linked register still carrying the pre-mutation bounds.

Attack sequence (attacker is an ordinary KYC'd tranche holder, epoch running, instant withdrawals enabled):

1. During epoch E the attacker calls `requestInstantWithdraw` via the CDO, so `instantWithdrawsRequestsByEpoch[attacker][E]` and `instantWithdrawClaimsByEpoch[E]` are set to `X`.
2. The CDO collects only part of the instant queue (`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` but leaves it nonzero — a partially prefunded instant queue, which `finalizeDefaultRecovery` explicitly anticipates at lines 683-696).
3. The attacker calls `claimInstantWithdrawRequest`. Because `_transferFundedClaim` only guards the *reserve* post-default, the strategy pays the full `X` from its underlying balance even though part of `X` was never funded. The aggregate is zeroed; the per-epoch counters are not.
4. The borrower then defaults within epoch E and `finalizeDefaultRecovery` runs while `epochNumber == E`. It adds the stale `instantWithdrawClaimsByEpoch[E] = X` to `defaultPendingClaimBasis()` (double-counting money already paid out) and computes `prefundedReserve = X - pendingInstantWithdraws`, inflating `defaultRecoveryReserve` by funds that no longer exist in the contract.
5. Result: `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is computed against phantom backing. Either the inflated basis dilutes honest claimants (they recover less than entitled), or the inflated `recoveryPrice` lets early recovery claimants (including the attacker, via `postDefaultRequests` or a second defaulted receipt) drain the real reserve at an overstated rate, and later claimants' `_transferDefaultRecovery` calls underflow/revert — permanent freezing of their recovery.

### Impact Explanation
Direct insolvency/theft from the isolated default-recovery pool: honest defaulted-epoch claimants receive a diluted `defaultRecoveryPrice`, or late claimants' claims revert when `defaultRecoveryReserve` was inflated by phantom prefunded amounts and earlier claims consumed the real balance. The attacker profits by claiming `X` once at par pre-default and having `X` counted again into the basis that determines everyone's recovery. Loss magnitude is bounded by the instant-withdraw queue size at default, which can be a large fraction of vault TVL.

### Likelihood Explanation
Requires an epoch where instant withdrawals are enabled, the instant queue is only partially prefunded (`pendingInstantWithdraws != 0` while claims are payable), a user claims before `stopEpoch`/default, and the borrower defaults in the same epoch. Partial prefunding and same-epoch default are states the code explicitly models (`defaultInstantWithdrawsFinalized`, `_defaultPrefundedInstantReserve`), so the path is reachable without privileged collusion — the borrower default itself is a market event, not attacker action. Likelihood is moderate; the accounting flaw is unconditional once the sequence occurs.

### Recommendation
In `claimInstantWithdrawRequest`, break the tie exactly as `_claimDefaultedInstantWithdrawRequest` does: after zeroing the aggregate, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount (guarding against underflow for receipts from older epochs, ideally by clearing each non-current epoch entry too or tracking the request epoch). Alternatively, pay out only the funded portion and leave the unfunded remainder claimable post-finalization through `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test — place in test/foundry/, modeled on IdleCreditVault.t.sol helpers.
// Assumes: standard epoch variant, instant withdrawals enabled
// (cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false) as in
// testClaimInstantWithdrawRequest).

function testStaleInstantEpochCountersCorruptRecovery() public {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);              // enter epoch #1

    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // Attacker deposits and starts an epoch
    uint256 amount = 100e6;
    uint256 tranches = _depositWithUser(attacker, amount);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // 1) instant withdraw request in epoch E
    _requestInstantWithdrawWithUser(attacker, tranches);   // cdoEpoch.requestInstantWithdraw path
    uint256 E = strategy.epochNumber();

    // 2) CDO collects only part of the instant queue -> pendingInstantWithdraws stays > 0
    //    (drive the normal partial-collection path used by getInstantWithdrawFunds)
    //    ... advance to instant delay, call the CDO instant-withdraw funding path ...

    // 3) attacker claims full funded payout; per-epoch counters remain set
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(strategy.instantWithdrawsRequests(attacker), 0);
    // BUG: these stay nonzero
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, E), tranches);
    assertGt(strategy.instantWithdrawClaimsByEpoch(E), 0);

    // 4) borrower defaults inside epoch E; finalize recovery
    deal(defaultUnderlying, borrower, 0, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);                     // defaulted
    // finalizeDefault(...)
    // assert: defaultPendingClaimBasis() double-counts the already-paid tranches
    // assert: defaultRecoveryReserve includes phantom prefunded amount
    // 5) honest user's recovery claim reverts on defaultRecoveryReserve underflow
    //    or pays a diluted/insolvent price.
}
```

Caveat: I could not fully verify the CDO-side helper names for partial instant-queue funding (`getInstantWithdrawFunds`/`collectInstantWithdrawFunds` ordering) in the available snippets; the PoC needs those wired to the actual IdleCDOEpochVariant calls, but the stale-counter state (`instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` never cleared on the funded-claim path at lines 380-393) and its downstream consumption at lines 644-649 and 716-723 are directly confirmed in the code above.