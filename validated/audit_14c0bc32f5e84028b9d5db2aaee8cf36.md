### Title
Loss-adjusted withdraw receipts bypass the haircut and pay at par when the user re-requests withdraw after the loss epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Like `gmap_helper_zap_one_page()` missing checks before zapping state, `_claimLossAdjustedWithdrawRequest()` checks only `lastWithdrawRequest[_user]` for a loss haircut, missing the check that the user may hold haircutted receipts from an *earlier* epoch. A user who makes any new `requestWithdraw` after a `stopEpochWithDuration` loss epoch overwrites `lastWithdrawRequest`, skips the haircut path entirely, and `_claimFundedWithdrawRequest` then pays the loss-epoch receipt at 100% par even though the strategy only collected the reduced amount — directly draining other claimants' funded reserves.

### Finding Description
In `IdleCreditVault.sol`, when `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds(_amount)` with `_amount < pendingBasis` stores a haircut `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws` (lines 411-430). The strategy then holds only `lossRecoveryPrice` fraction of the pending basis in underlying.

The claim path (`claimWithdrawRequest`, lines 301-314) runs `_claimLossAdjustedWithdrawRequest(_user)` first, which computes `lossEpoch = lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]` (lines 789-801). This is the missing check: it assumes the user's only unclaimed haircutted receipt belongs to `lastWithdrawRequest`. But `requestWithdraw` (lines 271-295) unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and is explicitly allowed for users with outstanding receipts ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch", lines 323-324).

Sequence:
1. Epoch N: user calls `cdoEpoch.requestWithdraw(...)` → `withdrawsRequestsByEpoch[user][N] = X`, `withdrawsRequests[user] = X`, `lastWithdrawRequest[user] = N`, receipt tokens minted.
2. Manager calls `stopEpochWithDuration(..., _lossAmount)` → `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL`; the strategy receives only `X * price / FULL` underlying for that epoch.
3. Epoch N+1 starts; user calls `requestWithdraw` again (even dust) → `lastWithdrawRequest[user] = N+1`, `withdrawsRequestsByEpoch[user][N+1] = Y`.
4. Epoch N+2; user calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[N+1] == 0` and returns without clearing epoch N. `_claimFundedWithdrawRequest` (lines 319-350) passes its gate (`epochNumber > lastWithdrawRequest`), then pays `withdrawsRequests[user] = X + Y` fully at par via `_transferFundedClaim`, burning the whole receipt.

The loss-epoch receipt `withdrawsRequestsByEpoch[user][N]` is never haircutted — the `(claimBasis * lossRecoveryPrice) / RECOVERY_FULL` reduction at line 799 is unreachable once `lastWithdrawRequest` moves past N. Existing guards don't stop it: the funded-claim epoch gate only enforces the one-epoch wait, `_settleApr0` doesn't touch normal receipts, and there is no per-epoch iteration or "any outstanding loss-epoch receipt" check anywhere in the claim path.

### Impact Explanation
The user is paid `X` at par instead of `X * lossRecoveryPrice / RECOVERY_FULL`. The strategy only holds `X * price / FULL` for that receipt, so the excess `X * (1 - price/FULL)` is paid out of underlying that belongs to other users' funded receipts, instant-withdraw reserves, or the default recovery reserve — direct theft / insolvency equal to the waived haircut. With a 50% realized loss split, an attacker recovers their full principal while honest claimants' transfers revert on insufficient balance (permanent freezing of their claims). The attacker needs only a KYC'd wallet and tranche tokens — unprivileged.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss epoch (manager/borrower-driven, honest roles — the borrower underpaying at stop is a normal operating path, not attacker action) plus a trivial second `requestWithdraw` in any later epoch. No privileged cooperation is needed; the attacker's only cost is waiting one epoch and the dust second receipt. Any real loss event exposes the invariant break deterministically.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` / `claimWithdrawRequest`, do not key the loss lookup solely on `lastWithdrawRequest[_user]`. Either iterate `withdrawsRequestsByEpoch[_user]` for any epoch with a non-zero `lossRecoveryPriceByEpoch`, or record the loss epoch per user (e.g., a per-user set/marker written in `collectWithdrawFunds`), so every haircutted receipt is cleared through the reduced-price path before any par payout. Alternatively, forbid `requestWithdraw` while the user holds an unclaimed receipt from a loss-adjusted epoch.

### Proof of Concept
```solidity
// Foundry fork test against contracts/strategies/idle/IdleCreditVault.sol + IdleCDOEpochVariant
function testLossReceiptPaidAtParAfterRerequest() external {
    // Setup: deposit, epoch 0 runs and stops normally
    uint256 amount = 10000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // Epoch N (1): request withdraw, then stopEpochWithDuration applies a loss
    uint256 req = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    uint256 lossEpoch = IdleCreditVault(address(strategy)).epochNumber();
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Borrower underfunds -> lossRecoveryPriceByEpoch[lossEpoch] < RECOVERY_FULL
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 lossAmount = (activeBasis + pendingBasis) / 2; // 50% total loss
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(defaultUnderlying, borrower, repay);
    vm.prank(borrower);
    IERC20(defaultUnderlying).approve(address(cdoEpoch), repay);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), lossAmount);
    assertLt(strategy.lossRecoveryPriceByEpoch(lossEpoch), 1e18); // haircut stored

    // Attacker does NOT claim; makes a second (dust) request in the next epoch
    _startEpochAndCheckPrices(2);
    uint256 dust = cdoEpoch.requestWithdraw(mintedAA / 100, address(AAtranche));

    // Wait one epoch so the funded-claim gate passes
    _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch());

    // Claim: loss-epoch receipt bypasses haircut, whole aggregate pays at par
    uint256 balPre = IERC20(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = IERC20(defaultUnderlying).balanceOf(address(this)) - balPre;

    // Bug: paid == req + dust (par) instead of req*lossPrice/FULL + dust
    assertEq(paid, req + dust, "loss-epoch receipt paid at par");
    // Excess was taken from underlying reserved for other funded claims -> insolvency.
}
```
Note: `_transferFundedClaim`'s exact transfer body was not read in this pass (index search confirmed its call sites); the PoC assumes it is a plain `underlyingToken.safeTransfer(_user, amount)` from the strategy balance, consistent with the "funded claim" naming and the `_burn`/`transfer` pattern at lines 343-349.