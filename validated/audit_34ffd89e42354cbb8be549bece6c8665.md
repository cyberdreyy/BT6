### Title
Loss-epoch withdraw receipts escape the recovery haircut when a newer request truncates the claim lookup to `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report (CVE-2025-27837) describes a lookup resolved through a *truncated identifier*, causing access to a resource the caller should not reach. The analog in this codebase is the loss-adjusted withdrawal claim path: `_claimLossAdjustedWithdrawRequest` resolves the user's claimable loss epoch through a single truncated key — `lastWithdrawRequest[_user]` — instead of the set of epochs in `withdrawsRequestsByEpoch`. Any earlier loss-epoch receipt is silently skipped and then paid **at par** by `_claimFundedWithdrawRequest`, which never consults `lossRecoveryPriceByEpoch`.

### Finding Description
In `IdleCreditVault.requestWithdraw`, each request appends to `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]`, and overwrites `lastWithdrawRequest[_user] = currentEpoch`. The comments explicitly allow stacking requests across epochs ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests").

On a `stopEpochWithDuration(..., _lossAmount)` the vault records `lossRecoveryPriceByEpoch[epoch]` and the borrower only funds `pendingToFund` — the haircutted amount — for that epoch's receipts (`previewLossAdjustedWithdrawFunds`).

At claim time (`claimWithdrawRequest`):

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];              // truncated key: only ONE epoch
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;                   // loss in an older epoch => skipped entirely
    ...
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;   // haircut applied only to that epoch
}

function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    ...
    uint256 normalAmount = withdrawsRequests[_user];             // aggregate across ALL epochs
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    _burn(_user, normalAmount + apr0PrincipalAmount);
    ...
    _transferFundedClaim(_user, amount);                         // paid at par, no per-epoch loss check
}
```

`_clearWithdrawClaimForEpoch` only clears the single `_claimEpoch` passed in, so a receipt from epoch N (with `lossRecoveryPriceByEpoch[N] != 0`) survives in `withdrawsRequestsByEpoch[_user][N]` and in the aggregate `withdrawsRequests[_user]` whenever `lastWithdrawRequest[_user]` was overwritten by a later request in epoch N+1. The funded path then pays that loss-epoch receipt at 100% even though the vault only received the haircutted funding for it.

Sequence (running epoch mode with loss support, e.g. `stopEpochWithDuration`):

1. Epoch N: attacker (KYC-passing lender / tranche holder) calls `requestWithdraw` for amount A.
2. `stopEpochWithDuration` is executed with `_lossAmount > 0`; `lossRecoveryPriceByEpoch[N] = r < RECOVERY_FULL`; borrower funds only `A * r / RECOVERY_FULL` for that receipt.
3. Epoch N+1 (new buffer/epoch): attacker requests another withdraw B. `lastWithdrawRequest[attacker]` is overwritten to N+1.
4. After epoch N+1 ends and requests are funded, attacker calls `claimWithdrawRequest`. `lossRecoveryPriceByEpoch[N+1] == 0`, so the loss path returns 0; `_claimFundedWithdrawRequest` pays `(A + B)` at par.
5. Attacker receives `A` instead of `A * r / RECOVERY_FULL`. The difference `A * (1 - r/RECOVERY_FULL)` is drawn from funds earmarked for other pending claims, instant-withdraw liquidity, or the contract's residual balance.

### Impact Explanation
Direct overpayment/insolvency: the attacker extracts the haircut that should have been socialized onto their receipt. The vault only holds `A * r` of funding for that receipt but pays `A`, so the excess `A * (1 - r)` is stolen from other users' funded claims or leaves the last claimants unable to withdraw (permanent freezing of their payouts). Quantified loss scales with the attacker's epoch-N request size and the loss magnitude; e.g. with r = 50%, a 1M USDC receipt steals 500k USDC.

### Likelihood Explanation
Requires only an unprivileged lender: request a withdraw before an epoch that ends with a `stopEpochWithDuration` loss, then place a second request in the following epoch. Both actions are normal protocol usage. The preconditions are (a) the vault supports loss-adjusted epochs (present in `IdleCDOEpochVariant.stopEpochWithDuration`), and (b) the borrower funds only the loss-adjusted amount — which is exactly what `previewLossAdjustedWithdrawFunds` computes, so the funding shortfall is structural. No privileged or malicious role is needed; honest manager calls supply the sequencing.

Uncertainty I could not fully verify in the available context: whether `requestWithdraw` or `_settleApr0` contains an additional guard that forces claiming an existing loss-epoch receipt before a new request is recorded. The code comments explicitly permit stacked unclaimed requests, and `_claimFundedWithdrawRequest` shows no per-epoch loss check, so the gap appears real, but a PoC should confirm step 3 succeeds while an unclaimed loss-epoch receipt exists.

### Recommendation
Resolve loss-adjusted claims per epoch, not via the single `lastWithdrawRequest` slot:

- Iterate/track all epochs in `withdrawsRequestsByEpoch[_user]` that have a non-zero `lossRecoveryPriceByEpoch`, or keep a per-user list/bitmap of unsettled loss epochs.
- In `_claimFundedWithdrawRequest`, before paying `withdrawsRequests[_user]` at par, subtract any basis belonging to epochs with `lossRecoveryPriceByEpoch[epoch] != 0` and route them through `_claimLossAdjustedWithdrawRequest` (or revert until they are claimed at the haircut).
- Alternatively, revert `requestWithdraw` when the user holds an unclaimed receipt in an epoch with a recorded `lossRecoveryPriceByEpoch`.

### Proof of Concept
Foundry fork test sketch (place in `test/foundry/IdleCreditVault.t.sol`, reusing helpers `_requestWithdrawWithUser`, `_stopCurrentEpochWithApr`, `stopEpochWithDuration`):

```solidity
function testLossEpochReceiptPaysAtParAfterNewRequest() external {
    // --- epoch N: attacker requests withdraw ---
    uint256 reqA = 100_000 * ONE_SCALE;
    idleCDO.depositAA(reqA + 200_000 * ONE_SCALE);          // attacker + other liquidity
    _startEpochAndCheckPrices(0);
    uint256 trancheA = IERC20(AAtranche).balanceOf(address(this)) / 3;
    cdoEpoch.requestWithdraw(trancheA, address(AAtranche)); // receipt A in epoch N

    // --- stop epoch N with a 50% loss (borrower funds haircut amount only) ---
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 loss = cdoEpoch.getContractValue() / 2;
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(loss);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pendingToFund);
    vm.prank(borrower); IERC20(defaultUnderlying).approve(address(cdoEpoch), type(uint256).max);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(aprX, 0, cdoEpoch.epochDuration(), loss);
    // lossRecoveryPriceByEpoch[N] now set; vault holds only ~50% for receipt A

    // --- epoch N+1: attacker files a SECOND request, truncating lastWithdrawRequest ---
    vm.prank(manager); cdoEpoch.startEpoch();
    cdoEpoch.requestWithdraw(smallTrancheAmount, address(AAtranche)); // lastWithdrawRequest -> N+1

    // --- end epoch N+1 normally, fund pendingWithdraws ---
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws());
    vm.prank(manager); cdoEpoch.stopEpoch(0, cdoEpoch.expectedEpochInterest());

    // --- claim: loss path sees epoch N+1 (no loss) -> funded path pays A + B at par ---
    uint256 balPre = IERC20(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = IERC20(defaultUnderlying).balanceOf(address(this)) - balPre;

    uint256 haircutExpected = reqAatBasis * strategy.lossRecoveryPriceByEpoch(epochN) / RECOVERY_FULL;
    assertGt(paid, haircutExpected); // attacker received full par on the loss-epoch receipt
}
```

Expected result: `paid` includes the full epoch-N basis at par, exceeding the `lossRecoveryPriceByEpoch[N]`-adjusted funding the vault actually received, draining other claimants' funds.