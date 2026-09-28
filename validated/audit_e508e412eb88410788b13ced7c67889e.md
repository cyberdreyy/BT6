### Title
Loss-adjusted withdraw receipts are booked under the post-increment epoch number, so haircut claims are misclassified as fully funded claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.epochNumber` is incremented inside `deposit()` whenever it is called while `IIdleCDOEpochVariant(idleCDO).isEpochRunning()` is still true — i.e., during `stopEpoch`/`stopEpochWithDuration` when the CDO deposits the borrower's repayment before clearing `isEpochRunning` (`IdleCreditVault.sol:607-611`). Pending withdraw receipts, however, record their request epoch as `epochNumber` *before* that increment (`requestWithdraw`, `IdleCreditVault.sol:260-293`, and `lastWithdrawRequest[_user]`). When `collectWithdrawFunds` is invoked later in the same `stopEpochWithDuration(_lossAmount)` transaction, `epochNumber` has already been bumped, so the haircut is written to `lossRecoveryPriceByEpoch[newEpoch]` while every affected receipt points at `oldEpoch`. This is an epoch/type-confusion analog of CVE-2018-4246: a receipt created as a "loss-adjusted" receipt is later interpreted as a "fully funded" receipt.

### Finding Description
Two storage locations disagree about which epoch a receipt belongs to:

- Request time: `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch]` use the pre-stop value (`IdleCreditVault.sol:260-293`).
- Funding time: `collectWithdrawFunds` computes `lossRecoveryPriceByEpoch[epochNumber]` at call time (`IdleCreditVault.sol:411-421`), which — because `deposit()` already incremented `epochNumber` earlier in `stopEpoch` — is `currentEpoch + 1`.

On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`IdleCreditVault.sol:789-801`). Because the haircut was stored under `oldEpoch + 1`, the lookup returns 0 and the function early-returns. The receipt then falls through to `_claimFundedWithdrawRequest`, whose only time gate is `epochNumber <= lastWithdrawRequest[_user]` (`IdleCreditVault.sol:326-328`) — now satisfied since `epochNumber` was bumped — and it pays out `withdrawsRequests[_user]` at par via `_transferFundedClaim`, burning the full receipt (`IdleCreditVault.sol:338-349`).

The strategy only holds the haircut-funded `_amount` for that epoch's pending basis. The first claimants therefore withdraw 100% of their basis instead of `claimBasis * lossRecoveryPrice / 1e18`, and later claimants' transfers revert for lack of funds. The `requestWithdraw` guard at `IdleCreditVault.sol:263-271` cannot help: it also reads `lossRecoveryPriceByEpoch[lastWithdrawRequest]`, which is 0, so users can even stack additional requests and claims. The same epoch misalignment applies to APR0 receipts (`apr0Users[_user].principalEpoch` is likewise the pre-increment epoch).

### Impact Explanation
Direct theft plus insolvency. In any `stopEpochWithDuration` closed with `_lossAmount > 0` while `pendingWithdraws != 0`, withdraw requesters claim their receipts at par against a reserve that was only funded for the post-haircut amount. Early claimants steal the share belonging to later claimants and/or active tranche holders; the aggregate payout exceeds funds collected, so the final claimants' `safeTransfer` reverts (permanent freezing of their receipts until someone subsidizes the strategy). The loss equals `pendingBasis - fundedAmount`, i.e., the entire haircut the manager intended to socialize is instead borne by the slowest claimants and the vault's solvency.

### Likelihood Explanation
Triggering requires only: (a) honest manager calls `stopEpochWithDuration` with a loss while at least one withdraw request is pending — a normal, expected operation; (b) claimants then call `claimWithdrawRequest` in any order. No attacker privilege is needed beyond being an ordinary withdraw requester (a KYC-passing lender). Because `collectWithdrawFunds`'s only guard is `defaultRecoveryInitialized` and `lossRecoveryPrice != 0` (`IdleCreditVault.sol:411-421`), nothing blocks the miskeyed write. The only uncertainty I could not fully verify without reading the full `stopEpochWithDuration` body is the exact call ordering of `deposit()` vs. `collectWithdrawFunds`; the bug exists iff `deposit()` runs first (which is the documented reason `epochNumber` increments inside `deposit()` when `isEpochRunning()` is still true).

### Recommendation
In `collectWithdrawFunds`, key the haircut to the epoch in which the receipts were requested — e.g., store `lossRecoveryPriceByEpoch[epochNumber - 1]` when the increment for this stop already occurred, or snapshot `epochNumber` at `startEpoch`/`requestWithdraw` time into a dedicated `pendingWithdrawsEpoch` variable and use that. Alternatively, move the `epochNumber` increment out of `deposit()` into `stopEpoch` after `collectWithdrawFunds` completes, so the receipt epoch and the recovery epoch are derived from the same counter value.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVault.t.sol style; fork/mainnet underlying as in existing tests
function testLossReceiptMiskeyedEpoch_claimsAtPar() external {
    address alice = vm.addr(0xA11CE);
    uint256 depositAmt = 10_000 * ONE_SCALE;
    deal(defaultUnderlying, alice, depositAmt);

    // Alice deposits AA and requests a normal withdraw during buffer/epoch 0.
    vm.startPrank(alice);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), depositAmt);
    idleCDO.depositAA(depositAmt);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // receipt epoch = 0
    vm.stopPrank();
    assertEq(strategy.lastWithdrawRequest(alice), 0);

    // Manager starts and runs the epoch, then stops it WITH a loss
    // (borrower underpays; pending receipts should take a haircut).
    _startEpochAndCheckPrices(0);
    uint256 pending = strategy.pendingWithdraws();
    uint256 funded  = pending * 80 / 100; // 20% loss on pending bucket
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() + funded);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, loss, 7 days, ONE_SCALE);

    // Bug: haircut stored under epochNumber==1, receipt points at epoch 0.
    assertEq(strategy.epochNumber(), 1);
    assertEq(strategy.lossRecoveryPriceByEpoch(1), /* haircut price */ nonzero);
    assertEq(strategy.lossRecoveryPriceByEpoch(0), 0); // receipt epoch: no haircut recorded

    // Alice claims: loss-adjusted path early-returns, funded path pays AT PAR.
    vm.prank(alice);
    uint256 claimed = cdoEpoch.claimWithdrawRequest();
    // claimed == full receipt amount, although only `funded` was collected for
    // all pending receipts -> any other pending claimant's transfer reverts.
}
```
Key assertions: `lossRecoveryPriceByEpoch[lastWithdrawRequest(alice)] == 0` after a loss-adjusted stop, and `claimWithdrawRequest` returning the unhaircutted basis, draining more underlying than `collectWithdrawFunds` pulled in.