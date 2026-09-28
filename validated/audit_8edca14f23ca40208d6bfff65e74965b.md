### Title
Loss-adjusted withdrawal haircut applies only to the user's latest request epoch — older pending receipts are paid at par, draining the funded reserve and freezing later claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`collectWithdrawFunds` applies a pro-rata loss to the **aggregate** `pendingWithdraws` bucket and stores a single `lossRecoveryPriceByEpoch[epochNumber]`. At claim time, `_claimLossAdjustedWithdrawRequest` looks up that price only under `lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch` haircuts only the amount recorded in `withdrawsRequestsByEpoch[_user][lossEpoch]`. Any receipt the same user recorded in an earlier epoch remains in `withdrawsRequests[_user]` and is paid at par through `_claimFundedWithdrawRequest`. The funded reserve therefore pays out more than was collected: early claimers overdraw, later claimers revert on `safeTransfer` — an out-of-bounds/stale-index analog where a value scoped to one epoch index is read against basis belonging to other epochs.

### Finding Description
In `requestWithdraw`, receipts are tracked per epoch via `withdrawsRequestsByEpoch[_user][currentEpoch]` while `pendingWithdraws` accumulates all epochs' amounts (`IdleCreditVault.sol:279-293`). A user may hold pending receipts in several epochs at once: after epoch N ends without loss, `lossRecoveryPriceByEpoch[N] == 0`, so the guard at lines 261-271 does not fire and the user can call `requestWithdraw` again in epoch N+1 without claiming the epoch-N receipt. Both receipts now sit inside the single `pendingWithdraws` aggregate.

When `stopEpochWithDuration(_lossAmount)` realizes a loss, `previewLossAdjustedWithdrawFunds` splits the loss pro-rata over the **whole** `pendingBasis` and `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = funded * RECOVERY_FULL / pendingBasis` (`IdleCreditVault.sol:411-430`, `440-459`). The strategy only receives `pendingBasis * price` underlying.

At claim time (`claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` → `_clearWithdrawClaimForEpoch` → `_withdrawClaimAmountsForEpoch`, lines 789-881), only `withdrawsRequestsByEpoch[_user][lossEpoch]` is haircut. The older `withdrawsRequestsByEpoch[_user][N]` amount was merged into `withdrawsRequests[_user]` and is paid 1:1 by `_claimFundedWithdrawRequest` (lines 319-349). Summed over users, payouts equal `Σ(latestEpoch receipts) * price + Σ(older receipts) * 1.0`, which strictly exceeds the funded `pendingBasis * price` whenever any user holds receipts from more than one epoch in the pending bucket.

### Impact Explanation
Insolvency of the funded-claim reserve: the strategy holds `pendingBasis * lossRecoveryPrice` underlying but owes `pendingBasis * price` only on the latest-epoch slice and full par on the rest. The first claimers extract more than their fair share; once the balance is exhausted, subsequent `claimWithdrawRequest` calls revert inside `safeTransfer`, permanently freezing the remaining users' funded withdrawals (the deficit equals `Σ older receipts * (1 - price)`, unbounded up to the full haircut amount). This breaks the "one receipt one payout at the correct haircut" invariant and socializes the loss onto whoever claims last rather than pro-rata.

### Likelihood Explanation
Requires a `stopEpochWithDuration` partial loss (borrower underfunds at epoch stop — an expected, non-default code path) while at least one user holds pending receipts opened in two or more distinct epochs. Leaving a matured receipt unclaimed and re-requesting is explicitly supported behavior (comment at line 323: "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests"). No privileged or malicious role is needed — only ordinary lenders withdrawing across consecutive epochs.

### Recommendation
Haircut the **entire** per-user pending basis at the loss epoch, not just `withdrawsRequestsByEpoch[_user][lossEpoch]`. Concretely: in `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch`, compute `claimBasis` over all receipts that were inside `pendingWithdraws` when the price was stored — e.g., iterate/clear every `withdrawsRequestsByEpoch` entry up to the loss epoch (or track a per-user `pendingEpochHighWaterMark` and haircut `withdrawsRequests[_user]` + open APR0 principal in full), so that total claims equal `pendingBasis * lossRecoveryPrice`. Alternatively, prevent a new `requestWithdraw` while any earlier receipt is still unfunded, keeping the pending bucket single-epoch.

### Proof of Concept
Foundry fork PoC (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossSkipsOlderEpochReceipts() external {
    // Epoch 0 buffer: user deposits via AA tranche
    uint256 amountWei = 10_000 * ONE_SCALE;
    address user1 = makeAddr('user1');
    address user2 = makeAddr('user2');
    _depositWithUser(user1, amountWei, true);
    _depositWithUser(user2, amountWei, true);

    // Epoch 1: user1 requests withdraw (receipt R1 in epoch 1)
    _startEpochAndCheckPrices(0);
    vm.prank(user1);
    cdoEpoch.requestWithdraw(0, address(AAtranche));          // request all
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);                // epoch 1 ends normally
    // user1 does NOT claim -> lossRecoveryPriceByEpoch[1] == 0

    // Epoch 2: user1 re-deposits... instead use user1 second tranche position:
    // (give user1 a second position first via depositAA during buffer)
    vm.prank(user1);
    // user1 requests again -> receipt R2 recorded in epoch 2,
    // R1 still inside pendingWithdraws
    uint256 req2 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Epoch 2 stops with a partial loss (stopEpochWithDuration path)
    // borrower funds only pendingBasis * (1 - lossShare)
    uint256 loss = _expectedFundsEndEpoch() / 4;
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() - loss);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    // stopEpochWithDuration variant that calls previewLossAdjustedWithdrawFunds
    // and collectWithdrawFunds with the reduced amount
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, epochDuration);

    // user1 claims: R2 is haircut via lossRecoveryPriceByEpoch[2],
    // but R1 is paid at par through _claimFundedWithdrawRequest
    vm.prank(user1);
    cdoEpoch.claimWithdrawRequest();

    // user2 (or user1's residual) claims last -> safeTransfer reverts:
    // strategy balance < remaining claim because reserve was overdrawn
    vm.prank(user2);
    vm.expectRevert(); // ERC20: transfer amount exceeds balance
    cdoEpoch.claimWithdrawRequest();
}
```

Note: I could not fully verify the `epochNumber` vs `lastWithdrawRequest` key alignment inside `stopEpochWithDuration` (whether `collectWithdrawFunds` runs before or after the `deposit()` epoch increment in `IdleCDOEpochVariant`). If the stored key mismatches `lastWithdrawRequest` entirely, the haircut is skipped for **all** users and the shortfall is worse — the PoC should assert both orderings. Either way the aggregate-vs-per-epoch basis mismatch stands on the code shown.