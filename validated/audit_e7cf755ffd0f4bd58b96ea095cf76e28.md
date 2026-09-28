### Title
Cross-epoch withdraw receipt escapes `stopEpochWithDuration` loss haircut and is paid at par — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.requestWithdraw` lets a user hold funded receipts in multiple epochs at once (`withdrawsRequestsByEpoch[_user][e1]` and `[e2]`), while `lastWithdrawRequest[_user]` only tracks the latest epoch. When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` computes one `lossRecoveryPrice` over the *aggregate* `pendingWithdraws` basis — but `_claimLossAdjustedWithdrawRequest` only clears the receipt recorded under `lastWithdrawRequest`'s epoch. The older-epoch remainder falls through to `_claimFundedWithdrawRequest` and is paid 1:1, even though the haircut and the funded amount already assumed it would share the loss.

### Finding Description
- `requestWithdraw` allows a second request without claiming the first, accumulating per-epoch entries while overwriting `lastWithdrawRequest` to the new `epochNumber` (`IdleCreditVault.sol:281-293`). `withdrawsRequests[_user]` and `pendingWithdraws` aggregate both epochs.
- On a loss stop, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` where `pendingBasis` includes *all* pending receipts across epochs (`IdleCreditVault.sol:411-421`), and only `_amount < pendingBasis` underlyings are transferred in.
- `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which resolves `lossEpoch = lastWithdrawRequest[_user]` and clears only `withdrawsRequestsByEpoch[_user][lossEpoch]` plus the matching APR0 bucket, then zeroes `lastWithdrawRequest` (`IdleCreditVault.sol:789-800`, `811-836`).
- Control then reaches `_claimFundedWithdrawRequest`: the epoch gate `epochNumber <= lastWithdrawRequest[_user]` passes because `lastWithdrawRequest` was just reset to 0, and it pays the full remaining `withdrawsRequests[_user]` — the older-epoch receipt — at par via `_transferFundedClaim` (`IdleCreditVault.sol:326-349`).

The bug class analog: a stale ("freed") receipt object persists after its accounting basis was consumed by the loss epoch, and is later dereferenced at full value — the older receipt escapes the haircut that the funded reserve was priced against.

### Impact Explanation
The reserve only contains `pendingToFund = pendingBasis * lossRecoveryPrice`. Paying the older-epoch receipt at par over-pays the attacker by `olderAmount * (1 - lossRecoveryPrice)`, which is drawn from the same funded pool owed to other users' haircutted receipts. The last claimant(s) of the loss epoch either receive less than `claimBasis * lossRecoveryPrice` or revert on insufficient balance — direct theft plus insolvency of the withdrawal reserve, quantifiable as the un-haircutted portion of every stale receipt. The attack also works repeatedly: after the claim, `lastWithdrawRequest` is 0 and `lossRecoveryPriceByEpoch[0]` is 0, so the stale receipt remains in `withdrawsRequests` and escapes *every* subsequent loss-adjusted epoch too.

### Likelihood Explanation
Requires no privileged action: any KYC'd lender requests a withdraw in epoch N, does not claim, requests again in epoch N+1 (a flow the code comments explicitly support — "if a user does not claim a withdraw request and instead requests another withdraw"), then a `stopEpochWithDuration(_lossAmount)` occurs (an honest-manager action). The guard at `IdleCreditVault.sol:263-271` only blocks re-requesting when the *latest* epoch already has a stored loss price; it does not prevent accumulating receipts across pre-loss epochs. Existing skim/Default/only-CDO guards do not cover this path.

### Recommendation
When a loss-adjusted price is recorded for `epochNumber`, either (a) apply `lossRecoveryPriceByEpoch` to *all* of the user's pending receipts — e.g., track per-user a `lossCutoffEpoch` and haircut every `withdrawsRequestsByEpoch` entry `<=` the loss epoch at claim time, or (b) revert `requestWithdraw` while any unclaimed prior receipt exists, or (c) store the haircut basis per user at collect time so stale entries cannot claim at par. Add a test where a user holds receipts in two epochs, a loss is realized, and total claims must not exceed `pendingToFund`.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultLossEscape.t.sol — fork per repo's foundry.toml
function testStaleReceiptEscapesLossHaircut() external {
    // deposit as a KYC'd user, epoch 1 running
    _depositWithUser(user, 100_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);

    // epoch 2 buffer: request withdraw #1, do NOT claim
    _stopCurrentEpochWithApr(10e18);           // epoch 2
    vm.prank(user);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // start epoch, then request withdraw #2 in a later epoch (still unclaimed)
    vm.prank(manager); cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _stopCurrentEpochWithApr(10e18);           // epoch 3
    vm.prank(user);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    // now withdrawsRequestsByEpoch[user] has entries in two epochs,
    // lastWithdrawRequest[user] == epochNumber, withdrawsRequests = sum

    // realize a loss: borrower funds only pendingToFund
    vm.prank(manager); cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = strategy.pendingWithdraws();
    uint256 loss = pending / 2;                // 50% loss on pending basis
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(loss);
    deal(defaultUnderlying, borrower, pendingToFund + cdoEpoch.expectedEpochInterest());
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), loss);
    // lossRecoveryPriceByEpoch[epochNumber] = 0.5e18 applied to TOTAL pending basis

    // attacker claims: latest-epoch piece haircut, stale piece paid at PAR
    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(user) - balPre;
    // got > pending * lossRecoveryPrice => over-paid vs funded reserve
    assertGt(got, pendingToFund);
    // a second user with an identical loss-epoch receipt now reverts or is shortchanged
}
```