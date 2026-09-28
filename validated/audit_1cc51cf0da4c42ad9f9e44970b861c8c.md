### Title
Loss-adjusted withdraw receipts only haircut the latest request epoch; older pending receipts are paid at par, draining the funded pool - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Like the kernel bug (disassoc frames applied to the STA interface without checking they originate from the connected BSSID), `IdleCreditVault.claimWithdrawRequest` processes withdrawal receipts without verifying that each pending receipt belongs to the epoch the recorded `lossRecoveryPriceByEpoch` applies to. `collectWithdrawFunds` computes a single recovery price over the aggregate `pendingWithdraws` — which includes receipts from *all* open epochs — but `_claimLossAdjustedWithdrawRequest` only applies the haircut to the receipt stored under `lastWithdrawRequest[_user]`. Any older unclaimed receipt falls through to `_claimFundedWithdrawRequest` and is paid at par out of the same haircutted funding, breaking the one-receipt-one-priced-payout invariant.

### Finding Description
- `requestWithdraw` (IdleCreditVault.sol:259-294) lets a user accumulate receipts in multiple epochs: it records `withdrawsRequestsByEpoch[_user][currentEpoch]` and overwrites `lastWithdrawRequest[_user] = currentEpoch`. Before any loss exists, the guard at lines 261-270 (`lossRecoveryPrice != 0`) does not fire, so a user can request in epoch N, not claim, and request again in epoch N+1 — ending with two pending receipts but `lastWithdrawRequest` pointing only at N+1.
- On a lossy stop, `collectWithdrawFunds` (lines 411-430) computes `lossRecoveryPrice = funded * RECOVERY_FULL / pendingBasis` where `pendingBasis = pendingWithdraws` includes **both** epochs' receipts, zeroes `pendingWithdraws`, and stores the price under `lossRecoveryPriceByEpoch[epochNumber]`. The vault only receives the haircutted amount.
- On `claimWithdrawRequest` (lines 301-314), `_claimLossAdjustedWithdrawRequest` (lines 789-801) resolves `lossEpoch = lastWithdrawRequest[_user]` = N+1 only, clears `withdrawsRequestsByEpoch[user][N+1]`, and pays `claimBasis * lossRecoveryPrice`. `_clearWithdrawClaimForEpoch` resets `lastWithdrawRequest` to 0 (lines 832-835), so `_claimFundedWithdrawRequest` (lines 319-350) then pays the remaining `withdrawsRequests[user]` — the epoch-N receipt — **at par** via `_transferFundedClaim`.
- The vault was only funded `pendingBasis * recoveryPrice`, but pays `epochN receipt * 1.0 + epochN+1 receipt * recoveryPrice`. The over-payment `(1 - recoveryPrice) * epochNAmount` is stolen from the pool backing all other pending claimers; the last claimers' `_transferFundedClaim` transfers revert or the reserve is drained.

### Impact Explanation
Direct value extraction and temporary freezing of other users' withdrawal claims. An attacker with receipts in two epochs captures the unhaircutted share of the older receipt; honest users who hold same-epoch receipts get `recoveryPrice` while the attacker effectively gets a blended price above `recoveryPrice`, and the deficit is socialized onto whoever claims last (their claim reverts for insufficient balance or consumes default-recovery reserve). Loss scales linearly: `(1 - recoveryPrice) * attackerOlderReceiptAmount`; with a 50% recovery price and a large earlier receipt the attacker nearly doubles their entitlement relative to other claimers.

### Likelihood Explanation
Requires a lossy `stopEpochWithDuration` (honest manager action, in scope) while the attacker holds unclaimed receipts from ≥2 distinct epochs — a state any unprivileged KYC'd lender can create cheaply by calling `cdoEpoch.requestWithdraw` in consecutive epochs without claiming. No privileged collusion needed; the attacker only times their own requests around an honest loss event. The `requestWithdraw` re-request guard only triggers *after* a loss price exists for the *latest* epoch, so it never prevents the multi-epoch position.

### Recommendation
Track the loss epoch per receipt rather than per user: apply `lossRecoveryPriceByEpoch[e]` to every `withdrawsRequestsByEpoch[user][e]` entry with a nonzero price, not just `lastWithdrawRequest[user]`. Concretely, in `_claimLossAdjustedWithdrawRequest`/`_claimFundedWithdrawRequest`, iterate or bucket pending receipts so that any receipt whose epoch has a recorded recovery price is haircut, and only receipts from epochs with `lossRecoveryPriceByEpoch[e] == 0` are paid at par — mirroring the mac80211 fix that drops frames whose BSSID does not match the associated AP.

### Proof of Concept
Foundry fork PoC (sketch):

```solidity
function testLossAdjustedOlderReceiptPaidAtPar() external {
    // deposit as attacker and victim, run epoch 0
    idleCDO.depositAA(ATTACKER_AMT);   // attacker
    _depositWithUser(victim, VICTIM_AMT, false);

    _startEpochAndCheckPrices(0);
    // attacker requests withdraw in epoch 0 buffer
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerTranches1, address(AAtranche));
    _stopEpochAndCheckPrices(0, apr, expectedFunds); // epoch -> 1, no loss

    // attacker does NOT claim; requests again in epoch 1
    _startEpochAndCheckPrices(1);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerTranches2, address(AAtranche));
    vm.prank(victim);
    cdoEpoch.requestWithdraw(victimTranches, address(AAtranche));

    // honest manager stops epoch 2 with a loss -> collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[2] < RECOVERY_FULL over ALL pending receipts
    _stopEpochWithLoss(lossAmount);

    // attacker claims: epoch-2 receipt haircut, epoch-0 receipt paid at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 attackerGot = underlying.balanceOf(attacker) - balPre;

    // attacker received more than pendingBasis*recoveryPrice share
    assertGt(attackerGot,
        (receiptEpoch0 + receiptEpoch2) * strategy.lossRecoveryPriceByEpoch(2) / RECOVERY_FULL);

    // victim's haircutted claim now reverts / underpays: pool insolvent
    vm.expectRevert(); // or assertLt on received amount
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: `strategy.withdrawsRequestsByEpoch(attacker, epoch0) != 0` while `lastWithdrawRequest(attacker) == epoch2`, and after the loss stop the attacker's payout exceeds the pro-rata haircutted amount by `(RECOVERY_FULL - recoveryPrice) * receiptEpoch0 / RECOVERY_FULL`.