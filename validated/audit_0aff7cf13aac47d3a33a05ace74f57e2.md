The bug class here is "function assumes a prior accounting/funding step happened, no check confirms it, so it computes and pays out amounts incorrectly." The strongest analog surface is the closed-pool withdraw-request path. Let me verify `_transferFundedClaim` and `collectWithdrawFunds` funding mechanics.### Title
Unfunded withdraw receipts are instantly claimable after pool close, letting a tranche holder drain funded reserves owed to other claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to `finishPrePurchasersMode()` assuming rewards were already distributed, `IdleCreditVault.requestWithdraw`/`claimWithdrawRequest` assume every minted receipt is (or will be) funded by a `stopEpoch` → `collectWithdrawFunds` transfer. Once the pool is closed (`epochEndDate == 0`, set in `_afterStopEpochWithDuration`/close-pool branch of `IdleCDOEpochVariant`), `requestWithdraw` deliberately skips `pendingWithdraws += _amount` because "a successfully closed pool already recalled all funds and has no later stopEpoch". But `_claimFundedWithdrawRequest` also treats `epochEndDate == 0` as "claim immediately", paying the receipt at par from the strategy's underlying balance even though no funding step ever accounted for it. A tranche holder can mint an unfunded receipt and instantly claim cash belonging to earlier, still-unclaimed funded receipts.

### Finding Description
Close-pool flow, `IdleCDOEpochVariant` lines 211-225: `expectedEpochInterest = 0`, `allowAAWithdrawRequest = true`, `epochDuration = 0`, `epochEndDate = 0` — withdrawals requests are re-enabled after closing.

Then `IdleCreditVault.requestWithdraw` (lines 243-295):
- `isClosed = epochEndDate() == 0` → the `pendingWithdraws += _amount` funding counter is skipped, and `withdrawsRequests[_user] += _amount` (or `apr0Users`) is still recorded, receipt strategy tokens are minted to the user.

Then `IdleCDOEpochVariant.claimWithdrawRequest` → `IdleCreditVault.claimWithdrawRequest` → `_claimFundedWithdrawRequest` (lines 319-350):
- The maturity check `if (epochEndDate() != 0 && epochNumber <= lastWithdrawRequest[_user]) revert` is bypassed because `epochEndDate() == 0`.
- `amount = withdrawsRequests[_user] (+ apr0 principal)` is paid via `_transferFundedClaim`, which only guards `defaultRecoveryReserve` (lines 897-907) and otherwise does `underlyingToken.safeTransfer(_user, _amount)` from whatever the strategy holds.

Invariant broken: one-receipt-one-funded-payout. Receipts minted post-close are never included in any `pendingWithdraws` settlement, yet are paid at par from reserves earmarked for earlier funded receipts (`collectWithdrawFunds` had already transferred exactly the old `pendingWithdraws` amount into the strategy at the closing `stopEpoch`).

Guards that do not stop it: `_skimDonatedAssets` (irrelevant), allow-flags (explicitly re-enabled post-close), `_ensureDefaultRecoveryInitialized` (only initializes), `_transferFundedClaim` reserve guard (only protects `defaultRecoveryReserve`, not unfunded-but-paid claims), `isWalletAllowed` (attacker is a KYC'd holder).

### Impact Explanation
Direct theft / insolvency: after a pool close, any underlying sitting in `IdleCreditVault` — funded-but-unclaimed normal/APR0 receipts, loss-adjusted funded amounts, instant-claim residue — can be drained by any tranche holder via `requestWithdraw` + `claimWithdrawRequest` in the same transaction. Loss equals `min(attackerReceipt, strategyUnderlyingBalance - defaultRecoveryReserve)`, up to 100% of still-unclaimed payouts; legitimate claimants' subsequent claims revert on `safeTransfer` or receive nothing (permanent freezing of unclaimed withdrawals).

### Likelihood Explanation
Requires the manager to close the pool (`stopEpoch` with `_interest == 1`), which is a supported, tested mode (`testClosePoolWithDurationLeavesDurationClosed`), and at least one funded receipt or residual underlying still unclaimed in the strategy — the normal state between close and user claims. Attacker needs only a KYC-passing tranche position; two permissionless calls in one transaction.

### Recommendation
In `IdleCreditVault.requestWithdraw`, either revert when `idleCDO.epochEndDate() == 0` (post-close requests are meaningless since no future `stopEpoch` funds them), or keep recording them in `pendingWithdraws` and gate `_claimFundedWithdrawRequest` on `withdrawsRequestsByEpoch[_user][epoch]` having been funded — e.g., track a `fundedEpoch`/cumulative funded amount and reject claims for epochs that never went through `collectWithdrawFunds`. Symmetric fix: in `IdleCDOEpochVariant.requestWithdraw`, `_checkNotAllowed(epochEndDate == 0)`.

### Proof of Concept
Foundry fork PoC (extends `test/foundry/IdleCreditVault.t.sol` fixture):

```solidity
function testPostCloseUnfundedReceiptStealsFundedClaims() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    address victim = makeAddr("victim");
    _depositWithUser(victim, amount, true); // second AA depositor

    // victim opens a normal withdraw request during buffer
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 victimReceipt = IdleCreditVault(address(strategy)).withdrawsRequests(victim);

    _startEpochAndCheckPrices(0);

    // borrower repays everything; manager closes the pool (_interest == 1)
    uint256 totFunds = _expectedFundsEndEpoch() + cdoEpoch.getContractValue();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, totFunds);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);   // closes pool: epochEndDate = 0, funds for victim receipt moved to strategy

    assertEq(cdoEpoch.epochEndDate(), 0);
    // strategy now holds victim's funded claim cash
    uint256 stratBal = underlying.balanceOf(address(strategy));
    assertGt(stratBal, 0);

    // attacker (this contract, still holding tranche tokens) mints an UNFUNDED receipt
    uint256 attackerBal = underlying.balanceOf(address(this));
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // pendingWithdraws NOT incremented (isClosed)
    cdoEpoch.claimWithdrawRequest();                 // pays at par immediately

    assertGt(underlying.balanceOf(address(this)) - attackerBal, 0, "unfunded claim paid out");

    // victim's funded claim is now bricked
    vm.prank(victim);
    vm.expectRevert(); // strategy under-funded: safeTransfer reverts
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: `pendingWithdraws` is unchanged by the post-close `requestWithdraw`, `claimWithdrawRequest` succeeds for the attacker because `epochEndDate() == 0` skips the `epochNumber <= lastWithdrawRequest` gate, and the victim's previously funded claim can no longer be paid.