### Title
Loss-adjusted withdraw receipts are paid at par because `lossRecoveryPriceByEpoch` is keyed to the post-increment `epochNumber` while receipts are keyed to the request epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The readlink-style race — a size measured under one snapshot but used under another — maps directly onto a *stale epoch index* bug in `IdleCreditVault`. `epochNumber` is incremented inside `deposit()` during `stopEpoch`, before `collectWithdrawFunds` stores the loss-recovery price. Withdrawal receipts, however, are tagged with `lastWithdrawRequest[user]` at request time. The recovery price is therefore stored under `epochNumber+1` while every receipt claims under its request epoch, so `_claimLossAdjustedWithdrawRequest` always sees `lossRecoveryPriceByEpoch[lossEpoch] == 0` and the haircut is silently skipped. Receipts minted before a lossy stop are paid **at par** out of an under-funded reserve, breaking the "one receipt one payout" and loss-waterfall invariants.

### Finding Description
In `IdleCDOEpochVariant.stopEpoch` (and `stopEpochWithDuration`), the sequence is:

1. `previewLossAdjustedWithdrawFunds(_lossAmount)` computes `pendingToFund = pendingBasis - pendingLoss` (pro-rata haircut on pending receipts), see `contracts/IdleCDOEpochVariant.sol:393` and `contracts/strategies/idle/IdleCreditVault.sol:440-460`.
2. `this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws)` is called while `isEpochRunning()` is still true (`IdleCDOEpochVariant.sol:408`). Inside, the strategy's `deposit()` executes `epochNumber += 1` (`IdleCreditVault.sol:607-611`).
3. Only then `_strategy.collectWithdrawFunds(_pendingWithdraws)` runs (`IdleCDOEpochVariant.sol:410`), and stores `lossRecoveryPriceByEpoch[epochNumber]` under the **already-incremented** epoch (`IdleCreditVault.sol:421`).

But `requestWithdraw` tags receipts with the pre-stop epoch: `lastWithdrawRequest[_user] = currentEpoch` (`IdleCreditVault.sol:282`) and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (`:293`).

At claim time, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`:789-792`). Because the price lives under `requestEpoch + 1`, the lookup returns 0, the function early-returns, and execution falls into `_claimFundedWithdrawRequest` (`:319-350`). The epoch-gate there — `epochNumber <= lastWithdrawRequest[_user]` — already passes (epochNumber was bumped), so it burns the receipt and pays `withdrawsRequests[_user]` in full via `_transferFundedClaim`.

The same off-by-one defeats the protective revert in `requestWithdraw` (`:263-271`), which checks `lossRecoveryPriceByEpoch[lastWithdrawRequest]` — also always 0 under the request epoch — so a user can stack further requests on top of an unclaimed haircut receipt.

### Impact Explanation
When `stopEpochWithDuration` realizes a loss and the borrower funds only `pendingToFund < pendingBasis`, the strategy holds strictly less underlying than the sum of receipt claims, yet each claimant is paid 100% of their basis. The shortfall equals `pendingLoss = _lossAmount * pendingBasis / totalBasis`. Early claimants (an unprivileged tranche holder can front-run claims, as `claimWithdrawRequest` is callable by the CDO for the msg.sender's receipt) extract full value while later claimants' `_transferFundedClaim` reverts on insufficient balance — theft of unclaimed withdrawal funds / permanent freezing of the residual receipts, proportional to the pending share of the realized loss. There is no privileged-role assumption: any tranche-token holder who requested a withdrawal in a lossy epoch benefits, and other ordinary users bear the insolvency.

### Likelihood Explanation
Triggering requires a `stopEpochWithDuration` with `_lossAmount > 0` while `pendingWithdraws > 0` — a normal, honest manager/borrower sequence (partial borrower repayment), not an edge case. Every loss-adjusted epoch miskeys the price, so the bug fires deterministically whenever the precondition occurs. The only mitigating factor is that partial-funding stops must be initiated by the honest manager, which the threat model allows to sequence around.

### Recommendation
Store the loss-recovery price under the epoch the receipts were recorded in. Options: (a) in `collectWithdrawFunds`, use `epochNumber - 1` (or track a separate `lastStopEpoch` set before `deposit()` bumps the counter); (b) snapshot `epochNumber` into `pendingWithdrawsEpoch` inside `requestWithdraw` and key `lossRecoveryPriceByEpoch` by the request epoch explicitly; (c) move the `epochNumber += 1` increment out of `deposit()` to the end of `stopEpoch`. Add a regression test that a loss-adjusted receipt claims exactly `claimBasis * lossRecoveryPrice / RECOVERY_FULL` and that a second `requestWithdraw` reverts while a haircut receipt is unclaimed.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultLossEpoch.t.sol
function testLossAdjustedReceiptPaidAtPar() public {
    // Setup: deposit AA, run epoch 0 normally (fixed APR).
    idleCDO.depositAA(100_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, /* epoch interest */);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);           // epochNumber -> 1

    // User requests withdraw during buffer (lastWithdrawRequest = 1).
    uint256 principal = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(user), address(AAtranche));
    vm.prank(user); // position user
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Borrower returns less than owed: _lossAmount > 0.
    // stopEpochWithDuration -> deposit() bumps epochNumber to 2,
    // collectWithdrawFunds stores lossRecoveryPriceByEpoch[2].
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(loss, /*...*/);

    // Assertion of the bug:
    assertEq(strategy.lossRecoveryPriceByEpoch(1), 0);   // user's receipt epoch: empty
    assertGt(strategy.lossRecoveryPriceByEpoch(2), 0);   // price stored one epoch ahead

    uint256 balBefore = underlying.balanceOf(user);
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    // Expected: principal * lossRecoveryPrice / RECOVERY_FULL
    // Actual:   principal paid at par -> overdraws strategy reserve
    assertEq(underlying.balanceOf(user) - balBefore, principal);
}
```

Uncertainty note: I verified the increment site (`deposit()` under `isEpochRunning()`, `IdleCreditVault.sol:607-611`) and the call order in `stopEpoch` (`IdleCDOEpochVariant.sol:408-410`), but I could not open `getFundsFromBorrower` itself to confirm it routes through `strategy.deposit()` while the epoch is still flagged running. If it instead transfers without calling `deposit`, the mismatch would not occur; a Foundry trace of a lossy `stopEpochWithDuration` would settle this in one run.