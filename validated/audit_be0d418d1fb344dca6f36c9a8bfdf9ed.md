### Title
Instant withdraw requests created after `getInstantWithdrawFunds` are never funded but are paid in full from other claimants' reserved funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` unconditionally once `allowInstantWithdraw` is set. `getInstantWithdrawFunds` collects `pendingInstantWithdraws` exactly once per epoch and then flips `allowInstantWithdraw = true`. A user who creates a new instant-withdraw request after that collection (but while the epoch is still running) increases `instantWithdrawsRequests`/`pendingInstantWithdraws` with no matching underlying transfer, yet can immediately claim the full amount, draining underlyings already collected for other users' funded instant claims.

### Finding Description
The flow is:

1. Epoch is running, APR was lowered so `lastEpochApr > currentApr + instantWithdrawAprDelta`, making `requestWithdraw` route to `requestInstantWithdraw` (IdleCDOEpochVariant.sol:761-769).
2. After `instantWithdrawDeadline`, the manager calls `getInstantWithdrawFunds`, which pulls `pendingInstantWithdraws` from the borrower via `collectInstantWithdrawFunds` and sets `allowInstantWithdraw = true` (IdleCDOEpochVariant.sol:563-570). `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` to zero and moves exactly that amount of underlying into the strategy (IdleCreditVault.sol:398-403).
3. The epoch remains running (`isEpochRunning` still true, `epochEndDate` not reached). `allowAAWithdrawRequest`/`allowBBWithdrawRequest` remain true and `requestWithdraw` does not check `isEpochRunning`, so a KYC'd attacker calls `requestWithdraw` again. The APR-drop condition still holds, so `requestInstantWithdraw` mints a fresh receipt and increases `instantWithdrawsRequests[attacker]` and `pendingInstantWithdraws` (IdleCreditVault.sol:356-374). No new funding will ever be collected: `getInstantWithdrawFunds` is a one-shot per-epoch call and the deadline logic already passed.
4. `claimInstantWithdrawRequest` only checks `allowInstantWithdraw` (IdleCDOEpochVariant.sol:977) and then in the vault burns the full `instantWithdrawsRequests[attacker]` and calls `_transferFundedClaim`, which transfers `amount` from the strategy's underlying balance (IdleCreditVault.sol:387-392, 897-906). There is no check that the attacker's receipt was included in the funded `pendingInstantWithdraws` snapshot.

The strategy's underlying balance at that moment is exactly the pool collected for earlier requesters (and, after default finalization, `defaultRecoveryReserve` — which `_transferFundedClaim` explicitly protects, but only against the recovery reserve, not against other instant claimants' funded balance). The unfunded receipt is thus paid out of other users' funded claims.

### Impact Explanation
Direct theft: the attacker receives `_underlyings` underlying that was funded by the borrower for *other* users' instant withdraw receipts. The loss is bounded only by the attacker's tranche balance; any instant-withdraw receipt created in the window between `getInstantWithdrawFunds` and `stopEpoch`/`epochEndDate` is claimable at par while unfunded. Equivalent impact: honest instant claimants' `claimInstantWithdrawRequest` reverts on insufficient balance, permanently freezing their funded claims.

### Likelihood Explanation
Requires: instant withdrawals enabled (`!disableInstantWithdraw`, non-programmable borrower), an APR decrease exceeding `instantWithdrawAprDelta` so the instant path triggers, and the attacker holding tranche tokens and passing `isWalletAllowed`. All are normal operating conditions; the attacker only needs to call `requestWithdraw` then `claimInstantWithdrawRequest` in the same running epoch after the funding transaction — a pure sequencing race, matching the CVE's race-condition bug class.

### Recommendation
Gate claims on funded inclusion, not just the global flag. Options: record a funded watermark (e.g. `instantWithdrawsFundedUpTo[epoch]` or a per-user `fundedInstantWithdraws[user]` snapshot taken inside `collectInstantWithdrawFunds`), and in `claimInstantWithdrawRequest` only pay the funded portion; alternatively freeze new instant requests after `getInstantWithdrawFunds` succeeds (set a flag checked in `requestInstantWithdraw`), or leave `allowInstantWithdraw` false until the next `startEpoch` resets it.

### Proof of Concept
```solidity
// Foundry fork PoC (scaffold — run against test/foundry/IdleCreditVault.t.sol setup)
function testPocUnfundedInstantClaim() external {
    // 1. Alice deposits, epoch 0 runs. Manager lowers APR by > instantWithdrawAprDelta.
    _depositWithUser(alice, 10_000e6, true);
    _startEpochAndCheckPrices(0);
    _setApr(currentApr - delta - 1); // trigger instant mode

    // 2. Alice and Bob request instant withdraws.
    vm.prank(alice); cdoEpoch.requestWithdraw(0, AA);
    vm.prank(bob);   cdoEpoch.requestWithdraw(0, AA);

    // 3. After deadline, manager funds pendingInstantWithdraws once.
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager); cdoEpoch.getInstantWithdrawFunds(); // allowInstantWithdraw = true

    // 4. Attacker requests AFTER funding snapshot — pendingInstantWithdraws grows again, never collected.
    vm.prank(attacker); cdoEpoch.requestWithdraw(0, AA);

    // 5. Attacker claims immediately; pays par out of Alice/Bob's funded reserve.
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - pre, 0);

    // 6. Bob's legitimate funded claim now reverts / underpays.
    vm.expectRevert();
    vm.prank(bob); cdoEpoch.claimInstantWithdrawRequest();
}
```