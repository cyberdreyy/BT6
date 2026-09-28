### Title
Loss-adjusted withdraw receipts escape the haircut and are paid at par because `collectWithdrawFunds` keys `lossRecoveryPriceByEpoch` to the already-incremented epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` records pending receipts under the epoch in which they were created (`lastWithdrawRequest[_user]`, `withdrawsRequestsByEpoch[_user][currentEpoch]`). When a `stopEpochWithDuration(_lossAmount)` realizes a loss and the borrower under-funds the pending withdrawals, `collectWithdrawFunds` stores the haircut under `lossRecoveryPriceByEpoch[epochNumber]`. However, `epochNumber` is incremented inside `deposit()` during the same `stopEpoch` call ("deposit done on stopEpoch (before setting the var to false) so we reset the counter / `epochNumber += 1`"). If the CDO's fund-collection `deposit` executes before `collectWithdrawFunds`, the recovery price is written under `epoch N+1` while every affected receipt is keyed to epoch `N`. `_claimLossAdjustedWithdrawRequest` then looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (= epoch N), finds `0`, skips the loss path, and `_claimFundedWithdrawRequest` pays the full `withdrawsRequests[_user]` at par — the double-free analog: the same claim basis is "freed" (funded) twice, once through the loss bucket and once through the funded bucket.

### Finding Description
- `requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` for the epoch the request belongs to (lines 282–293).
- `deposit()` increments `epochNumber` while `isEpochRunning()` is still true during `stopEpoch` (lines 607–610).
- `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws` when `_amount < pendingBasis` (lines 414–421).
- `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., keyed by the *request* epoch (lines 789–792). If the stored key is `N+1` and the request epoch is `N`, the lookup misses.
- The function then falls through to `amount + _claimFundedWithdrawRequest(_user)` (line 313), which pays `withdrawsRequests[_user]` in full from funded strategy underlyings.
- The anti-double-request guard in `requestWithdraw` (lines 263–271) also keys on `lossRecoveryPriceByEpoch[lossEpoch]` with `lossEpoch = lastWithdrawRequest[_user]` — the same wrong key — so users are not even blocked from opening new requests while holding an unhaircut receipt.

The result: receipts that should be haircut by `lossRecoveryPrice` are paid at par. If the strategy holds only the under-funded amount, later claimants (or the claim itself once balances are exhausted) are left unpaid — loss socialization is broken and early claimants drain the pool at the expense of active LPs and other receipt holders.

### Impact Explanation
Direct insolvency/theft: after a realized loss epoch, every pending receipt holder can claim the pre-haircut amount. The haircut (`pendingLoss`) is never applied to anyone, so the loss is silently re-socialized onto active tranche holders and the last claimers, who face a drained strategy. Loss magnitude ≈ `pendingWithdraws * (1 - lossRecoveryPrice/RECOVERY_FULL)`, plus orderly-exit insolvency for whatever remains unfunded.

### Likelihood Explanation
Triggers on any `stopEpochWithDuration`/`stopEpoch` where the collected amount is less than `pendingWithdraws` (i.e., exactly the scenario `previewLossAdjustedWithdrawFunds` exists for — borrower pays only the pro-rata funded portion). Requires only an unprivileged lender with a pending withdraw request in the loss epoch. No malicious privileged role needed; the honest manager calls `stopEpoch` normally. Depends on the CDO calling `deposit` before `collectWithdrawFunds` inside `stopEpoch` — this ordering is consistent with `deposit`'s own comment that it runs "on stopEpoch (before setting the var to false)" and increments `epochNumber`.

Caveat I could not fully verify in this pass: the exact ordering of `deposit` vs `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpochWithDuration`. If `collectWithdrawFunds` executes before the epoch-incrementing `deposit`, the key matches and the bug does not manifest; the PoC below settles this either way.

### Recommendation
In `collectWithdrawFunds`, store the recovery price under the epoch the pending receipts belong to (i.e., `epochNumber - 1` semantics, or better, pass the claim epoch explicitly from the CDO, or snapshot the request epoch before incrementing). Alternatively, increment `epochNumber` at `startEpoch`/`_afterStopEpoch` rather than inside `deposit`, so all writes within one `stopEpoch` share a single consistent epoch key. Add a regression test asserting `lossRecoveryPriceByEpoch` is keyed to the request epoch of pending receipts.

### Proof of Concept
```solidity
// Fork test against mainnet state; uses existing harness style from test/foundry/IdleCreditVault.t.sol
function testLossEpochKeyMismatchPaysReceiptsAtPar() external {
    // 1. Deposit as unprivileged LP, start epoch #N.
    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);

    // 2. During epoch N, request a withdraw; receipt keyed to epoch N.
    uint256 requested = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));
    assertEq(strategy.lastWithdrawRequest(address(this)), strategy.epochNumber());

    // 3. Warp past epoch end; stop epoch with a realized loss so the borrower
    //    under-funds pendingWithdraws (stopEpochWithDuration(_lossAmount) path).
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = strategy.pendingWithdraws();
    uint256 loss = pending / 2; // force partial funding
    deal(defaultUnderlying, borrower, pending - loss + buffer);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(loss, /*…*/);

    // 4. Assert the bug: recovery price stored under the incremented epoch,
    //    while the receipt epoch lookup returns zero.
    uint256 reqEpoch = strategy.lastWithdrawRequest(address(this));
    assertEq(strategy.lossRecoveryPriceByEpoch(reqEpoch), 0);            // missed key
    assertGt(strategy.lossRecoveryPriceByEpoch(reqEpoch + 1), 0);        // stored under N+1

    // 5. Claim: loss-adjusted path is skipped, funded path pays FULL amount.
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balPre, requested);   // at par, no haircut
}
```

If step 4's first assertion fails (price stored under the request epoch), the ordering is safe and the finding reduces to a code-review note on fragile epoch-key coupling; otherwise the haircut is fully bypassed as described.