### Title
Loss-adjusted withdraw receipts keyed by wrong epoch escape the haircut and claim at par, draining funded claims of other users - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` records a stop-epoch loss under `lossRecoveryPriceByEpoch[epochNumber]` in `collectWithdrawFunds`, but `_claimLossAdjustedWithdrawRequest` looks the loss up under `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, i.e. the epoch in which the user *requested* the withdrawal. Because `deposit()` increments `epochNumber` during `stopEpoch` before the collect step, the loss is stored under `requestEpoch + 1` while receipts carry `lastWithdrawRequest == requestEpoch`. The lookup returns 0, the loss-adjusted path is skipped, and the receipt falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests` basis at par even though the borrower only funded `pendingBasis - loss`.

### Finding Description
- At request time, `requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (`IdleCreditVault.sol:282-293`).
- At the next `stopEpoch`, the CDO routes repaid funds through `strategy.deposit`, which executes `epochNumber += 1` (`IdleCreditVault.sol:607-611`). `collectWithdrawFunds` then writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` under the *post-increment* epoch (`IdleCreditVault.sol:417-421`), while zeroing `pendingWithdraws` and pulling only the haircutted `_amount` from the CDO.
- On claim, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest(_user)`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the pre-increment epoch — finds 0 and returns (`IdleCreditVault.sol:789-792`).
- `_claimFundedWithdrawRequest` then passes its gate because `epochNumber (new) > lastWithdrawRequest[_user]` and pays `withdrawsRequests[_user]` in full via `_transferFundedClaim`, burning the receipt tokens (`IdleCreditVault.sol:326-349`).

The invariant "pending receipts absorb their pro-rata share of a realized stop-epoch loss" is broken: every loss-epoch receipt claims 100% while the strategy only received `pendingBasis × lossRecoveryPrice / RECOVERY_FULL`. There is no guard that catches this — `_transferFundedClaim` only protects `defaultRecoveryReserve`, which is 0 in the non-default loss path.

### Impact Explanation
The aggregate overpayment equals the loss assigned to the pending bucket (`pendingBasis - pendingToFund`). Early claimers drain underlying that belongs to other users' funded receipts (funded instant-withdraw claims, still-funded normal receipts, or reserve), so later claimants' transfers revert on insufficient balance — partial theft plus permanent freezing of the remainder, proportional to the realized loss. The receipt tokens are burned on each claim, so the damage is irreversible.

### Likelihood Explanation
Requires a `stopEpochWithDuration`-style loss (borrower under-funds `pendingWithdraws`) — an honest-manager action with a real loss, well within scope. Any pending withdraw request in that epoch triggers the mispatch. The only precondition is that the loss code path (`_amount < pendingBasis`, `defaultRecoveryInitialized`) executes, which is the normal loss-handling flow. Likelihood is tied to loss events, but whenever one occurs with pending receipts the mispricing is deterministic.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the same epoch identifier stored in receipts — i.e. snapshot the request epoch (e.g. `epochNumber` before the stop-time increment, or a dedicated `pendingWithdrawEpoch` captured when receipts accumulate) — or store the pending basis per epoch (`withdrawsRequestsByEpoch` aggregate) and iterate/settle it explicitly. Add an invariant test that after a loss-adjusted `collectWithdrawFunds`, the sum of all claims equals the funded `_amount`.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultLossEpoch.t.sol — fork test, standard epoch variant
function testLossAdjustedReceiptsEscapeHaircut() external {
    // 1. Deposit AA/BB, run epoch 0, stopEpoch (epochNumber -> 1).
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // 2. Buffer phase: two users request normal withdraws. Both get
    //    lastWithdrawRequest = epochNumber (e.g. 1), withdrawsRequestsByEpoch[u][1] set.
    uint256 req1 = cdoEpoch.requestWithdraw(mintedAA1, address(AAtranche)); // user1
    uint256 req2 = cdoEpoch.requestWithdraw(mintedAA2, address(AAtranche)); // user2 (second account)
    uint256 pendingBasis = strategy.pendingWithdraws(); // req1 + req2

    // 3. Epoch 1 runs; borrower under-funds: stopEpochWithDuration(loss).
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 loss = pendingBasis / 4; // 25% loss on pending bucket
    uint256 fund = pendingBasis - loss;
    deal(defaultUnderlying, borrower, fund + expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, expectedInterest, loss); // epochNumber -> 2 inside deposit()

    // lossRecoveryPriceByEpoch[2] = fund*RECOVERY_FULL/pendingBasis; [1] stays 0.
    assertEq(strategy.lossRecoveryPriceByEpoch(1), 0);
    assertGt(strategy.lossRecoveryPriceByEpoch(2), 0);

    // 4. user1 claims: _claimLossAdjustedWithdrawRequest reads epoch 1 -> skips
    //    haircut; _claimFundedWithdrawRequest pays req1 at PAR instead of 75%.
    uint256 bal1 = underlying.balanceOf(user1);
    vm.prank(user1);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(user1) - bal1, req1); // haircut bypassed

    // 5. Strategy balance now holds only `fund` for `req1+req2` of claims:
    //    user2's claim reverts on insufficient balance (frozen) or is paid
    //    from unrelated funded claims => insolvency of the pending bucket.
    vm.prank(user2);
    vm.expectRevert(); // underflow / SafeERC20 transfer failed
    cdoEpoch.claimWithdrawRequest();
}
```

Caveat: this finding depends on `deposit()` (the `epochNumber += 1` at `IdleCreditVault.sol:610`) executing before `collectWithdrawFunds` within the same `stopEpoch`/`stopEpochWithDuration` call in `IdleCDOEpochVariant.sol`. I could not read that ordering in the remaining budget; if `collectWithdrawFunds` is invoked strictly before the incrementing deposit, the keys align and the issue does not trigger — the PoC's step-4 assertion is the decisive check.