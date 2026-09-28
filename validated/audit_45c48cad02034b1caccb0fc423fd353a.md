### Title
Loss-adjusted withdraw receipts can claim at par because `lossRecoveryPriceByEpoch` is keyed by the post-increment `epochNumber` while user receipts point to the pre-increment `lastWithdrawRequest` epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The `time` segfault is a dangling-pointer/TOCTOU bug: state read at check time is invalidated by a mutation that happens on a different "thread" before the value is used. The vault analog is the epoch pointer used to index loss-recovery data: `collectWithdrawFunds` stores the haircut under the **current** `epochNumber`, but `_claimLossAdjustedWithdrawRequest` dereferences it through `lastWithdrawRequest[user]`, which still points at the epoch in which the request was made. When `stopEpochWithDuration`/`stopEpoch` bumps `epochNumber` before collecting funds, the receipt's epoch pointer is "dangling" — the haircut is stored under a key the user will never look up.

### Finding Description
In `IdleCreditVault.requestWithdraw`, the user's receipt epoch is recorded as `lastWithdrawRequest[_user] = currentEpoch` (`epochNumber` at request time). Requests are only made during the buffer phase, i.e. after the previous `stopEpoch` already incremented `epochNumber`, so a request pending at the next stop has `lastWithdrawRequest[user] == E` while the stop transitions the strategy to `E+1`.

When the borrower repays less than `pendingWithdraws`, `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` using the **new** epoch number and zeroes `pendingWithdraws`. Later, `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[_user]` (still `E`) and reads `lossRecoveryPriceByEpoch[E]`, which is `0`, so it returns early. Execution then falls into `_claimFundedWithdrawRequest`, which pays the user the full un-haircut `withdrawsRequests[_user]` from whatever underlying the strategy actually collected.

The same stale-pointer mismatch defeats the `requestWithdraw` guard at lines 263-271: it only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest]`, so users whose receipts were haircut under epoch `E+1` are free to open new requests immediately.

Consequences:
- Early claimants receive par payout from a reserve funded only for the haircut amount → direct theft of other claimants' shares.
- Once the funded balance is drained, later claimants' `_transferFundedClaim` reverts (or underflow on reserve checks) → permanent freezing of remaining withdrawal claims.
- The haircut accounting (`pendingWithdraws = 0`) is never reconciled, so the insolvency is silent.

### Impact Explanation
Any user with a pending withdraw receipt at a lossy `stopEpochWithDuration` can claim more than their loss-adjusted share. Aggregate theft equals `pendingBasis - fundedAmount` distributed first-come-first-served; last claimants lose up to 100% of their receipts (permanent freeze via revert). This breaks the "one receipt, proportional payout" and loss-waterfall invariants.

### Likelihood Explanation
Trigger requires only an honest `stopEpochWithDuration` with `_lossAmount > 0` while pending withdraws exist — no attacker privileges needed; any tranche holder with a pending receipt benefits by claiming early. Sequencing depends only on the internal order of `epochNumber` increment vs `collectWithdrawFunds` inside `stopEpoch`, which the comments confirm is bumped at stop time. (Caveat: the exact statement ordering in `stopEpoch` was not fully verifiable within the indexed excerpts; the PoC below confirms or refutes the mismatch empirically.)

### Recommendation
Store the loss recovery under the epoch the pending receipts were created in — i.e., pass the request epoch explicitly or record `lossRecoveryPriceByEpoch` keyed by `epochNumber - 1`/the epoch captured before the increment — or, more robustly, iterate/mark a global "latest loss epoch" that `_claimLossAdjustedWithdrawRequest` consults instead of `lastWithdrawRequest[_user]` alone. Alternatively, check `lossRecoveryPriceByEpoch` for all epochs `<= epochNumber` when clearing claims.

### Proof of Concept
```solidity
// test/foundry/LossEpochMismatch.t.sol — fork-style PoC on existing harness
function testLossReceiptEscapesHaircut() external {
    // 1. Deposit, start epoch 1 (running).
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    // 2. Stop epoch with zero loss -> buffer opens, epochNumber becomes 1.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // 3. Request withdraw during buffer; lastWithdrawRequest[user] == 1.
    uint256 req = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));
    assertEq(strategy.lastWithdrawRequest(address(this)), 1);

    // 4. Start epoch 2, borrower repays only part of pendingWithdraws + interest.
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = strategy.pendingWithdraws();
    // fund borrower for active interest but short the pending receipts by 50%
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + pending / 2);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1); // or stopEpochWithDuration loss path

    // 5. Haircut was stored under the incremented epochNumber (2), not 1.
    assertEq(strategy.lossRecoveryPriceByEpoch(1), 0);
    assertGt(strategy.lossRecoveryPriceByEpoch(2), 0);

    // 6. Claim pays the FULL un-haircut basis -> overpayment / later claimants revert.
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - balPre;
    assertEq(got, req); // par payout despite ~50% funded reserve
}
```
If step 5 shows the price stored under `epochNumber` while `lastWithdrawRequest` lags, the claim in step 6 overpays; a second claimant's `claimWithdrawRequest` then reverts in `_transferFundedClaim` for lack of balance, demonstrating theft plus permanent freezing.