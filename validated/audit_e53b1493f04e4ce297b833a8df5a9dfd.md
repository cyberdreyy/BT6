### Title
Instant withdraw receipts escape `stopEpochWithDuration` loss haircut and drain the loss-adjusted reserve — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` socializes a realized stop-epoch loss only across `pendingWithdraws` (normal receipts) by storing a `lossRecoveryPriceByEpoch[epochNumber]` haircut. Instant-withdraw receipts created in the same epoch are never haircut: `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and pays the full amount via `_transferFundedClaim`, and `lastWithdrawRequest`/`lossRecoveryPriceByEpoch` are never consulted for the instant path. The analog of the StringIO overread is a receipt claim that "reads past" what was actually funded for it — an instant receipt redeems at par against underlyings that include the reserve funded for haircut-bearing normal receipts.

### Finding Description
In `IdleCreditVault`:

- `requestInstantWithdraw` mints the user a 1:1 strategy-token receipt and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (lines 356-375). It does not set `lastWithdrawRequest[_user]`.
- On a lossy stop, `collectWithdrawFunds` is invoked with `pendingToFund < pendingBasis` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` (lines 411-421). Only `pendingWithdraws` (normal request basis) is haircut — `previewLossAdjustedWithdrawFunds` explicitly computes the pending-side loss only over `pendingBasis = pendingWithdraws` (lines 440-459). `pendingInstantWithdraws` is not part of the loss split.
- `_claimLossAdjustedWithdrawRequest` applies the haircut only when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` (lines 789-801). Instant requesters have `lastWithdrawRequest == 0` (or an unrelated older epoch), so nothing haircuts them.
- `claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim(_user, amount)` at par (lines 380-393).

The funded reserve in the strategy is fungible: `_transferFundedClaim` pays whoever claims first from the same `underlyingToken` balance that holds the loss-adjusted `pendingToFund` collected for normal receipts. An attacker holding an instant receipt from the loss epoch therefore redeems 100% while normal receipt holders from the identical epoch redeem `lossRecoveryPrice`, and if the attacker's instant claim is large enough it consumes underlyings earmarked for the haircut receipts, leaving later normal claimants undercollateralized (insolvency / direct theft).

Guard check: `requestWithdraw`'s loss-receipt guard (lines 263-271) only blocks a *new normal request* while an unclaimed loss receipt exists; it does not touch instant receipts. `_handleBorrowerDefault` does haircut instant receipts via `_claimDefaultedInstantWithdrawRequest`, but the soft-loss path (`stopEpochWithDuration` with `_lossAmount`) has no equivalent, which is the gap.

### Impact Explanation
Attacker is any KYC-passed lender. During a running epoch with instant withdraws enabled, the attacker calls `requestInstantWithdraw` for a large position. The epoch ends with a realized loss (`stopEpochWithDuration(_lossAmount)`) where the manager did not pre-fund instant requests via `getInstantWithdrawFunds`, or only partially. The attacker calls `claimInstantWithdrawRequest` and receives full par value while honest normal-request users of the same epoch receive only `lossRecoveryPrice`-adjusted payouts — and the attacker's claim can directly consume the reserve funded for those users, causing quantifiable theft equal to `instantAmount * (1 - lossRecoveryPrice)` plus potential insolvency of the remaining receipt reserve.

### Likelihood Explanation
Requires: (a) instant withdraws enabled (`disableInstantWithdraw == false`), (b) attacker submits an instant request while the epoch is running and before the instant-withdraw deadline, (c) the epoch closes with a nonzero `_lossAmount` via `stopEpochWithDuration` rather than a hard default (the default path does haircut instant receipts). These are all within normal honest-manager operation; the loss asymmetry is deterministic once pending instant receipts coexist with a lossy stop. Likelihood is moderate: it needs a loss epoch with outstanding unfunded instant receipts, but no privileged misbehavior is required — the manager simply may not call `getInstantWithdrawFunds` before the stop.

### Recommendation
Include `pendingInstantWithdraws` in the loss split in `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` (extend `lossRecoveryPriceByEpoch` coverage to instant receipts for that epoch), and apply the same haircut in `claimInstantWithdrawRequest` for receipts recorded via `instantWithdrawsRequestsByEpoch[_user][epochNumber]` when `lossRecoveryPriceByEpoch[epochNumber] != 0` — mirroring how `_claimDefaultedInstantWithdrawRequest` already uses `defaultRecoveryPrice`. Alternatively, require instant requests to be fully funded before a lossy stop is permitted.

### Proof of Concept
Foundry fork sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testInstantWithdrawEscapesLossHaircut() external {
    // enable instant withdraws, deposit with attacker + victim
    uint256 amt = 10000 * ONE_SCALE;
    _depositWithUser(attacker, amt, true);
    _depositWithUser(victim, amt, true);

    _startEpochAndCheckPrices(0); // epoch running

    // victim: normal withdraw request; attacker: instant withdraw request (before deadline)
    vm.prank(victim);
    cdoEpoch.requestWithdraw(victimShares, address(AAtranche));
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerAmount, address(AAtranche));

    // warp past epoch end; borrower repays with a realized loss
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 loss = pendingBasis / 2; // 50% loss on pending bucket
    // fund borrower with reduced amount, then:
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(newApr, 0, newDuration, loss);

    // strategy.collectWithdrawFunds stored lossRecoveryPriceByEpoch[epoch] < RECOVERY_FULL
    assertLt(strategy.lossRecoveryPriceByEpoch(lossEpoch), RECOVERY_FULL);

    // attacker claims instant receipt at par
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - pre, attackerAmount); // no haircut

    // victim claims normal receipt: haircut applied AND reserve may be drained
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest(); // receives < attacker-equivalent, or reverts on insolvency
}
```

Expected broken invariant: equal-epoch receipts must share losses pro rata ("loss waterfall"); instead the instant receipt redeems at 100% and can consume the reserve funded for haircut receipts.