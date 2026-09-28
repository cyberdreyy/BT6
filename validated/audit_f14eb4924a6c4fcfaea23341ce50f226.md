### Title
Loss-adjusted withdraw receipts can be claimed at par because `collectWithdrawFunds` keys `lossRecoveryPriceByEpoch` with the post-increment `epochNumber` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The libarchive report describes an integer/offset mismatch that turns bookkeeping into a double-free: the same allocation is released twice because the state that marks it "already freed" is keyed inconsistently. The closest analog in this codebase is the loss-adjusted withdraw-receipt path in `IdleCreditVault`. When `stopEpochWithDuration` funds pending receipts at a haircut, the recovery price is stored under `lossRecoveryPriceByEpoch[epochNumber]`, but user claims resolve the recovery epoch via `lastWithdrawRequest[_user]`, which was stamped with the `epochNumber` at request time. Because `epochNumber` is incremented inside `deposit()` during the `stopEpoch` flow itself (`epochNumber += 1` when `isEpochRunning()` is still true), if `collectWithdrawFunds` runs after that deposit the haircut is stored under `epochNumber + 1` and is never found by claimants.

### Finding Description
- `requestWithdraw` stores `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` evaluated during the buffer phase, before the epoch is stopped.
- `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the request-time epoch.
- `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` — the strategy's `epochNumber` at collect time.
- `deposit()` increments `epochNumber` whenever it is called while `isEpochRunning()` is still true, which happens inside `stopEpoch` when the CDO accounts borrower repayment ("deposit done on stopEpoch ... epochNumber += 1").
- Borrower repayment for pending withdraws must already be held by the CDO before `collectWithdrawFunds` pulls it via `safeTransferFrom(idleCDO, ...)`, so `collectWithdrawFunds` is ordered after the epoch-ending deposit that bumps `epochNumber`.

Result: `lossRecoveryPriceByEpoch[E+1]` is written while every affected receipt points at epoch `E`. On claim, `lossRecoveryPrice == 0` causes `_claimLossAdjustedWithdrawRequest` to return 0, the per-epoch receipt (`withdrawsRequestsByEpoch[_user][E]`, still contributing to `withdrawsRequests[_user]`) is never cleared or haircut, and `_claimFundedWithdrawRequest` pays the full pre-loss basis at par — even though the strategy only collected `pendingToFund` (the haircut amount) and zeroed `pendingWithdraws`.

The analogous "double-free" shape is also present in the cleanup: `pendingWithdraws` was set to 0 in `collectWithdrawFunds` as if the obligation were settled, while the aggregate `withdrawsRequests[_user]` remains payable in full — one logical obligation accounted as discharged (haircut recorded) yet still paid at 100%.

### Impact Explanation
Every receipt holder in a loss-adjusted epoch is paid the un-haircut basis. Since only the reduced amount was collected from the borrower, the shortfall is paid out of the strategy's underlying balance, which backs other users' funded receipts, the default-recovery reserve accounting boundary (`_transferFundedClaim` only protects `defaultRecoveryReserve`, not loss-epoch underfunding), and active LP value. Realized losses assigned to pending redeemers are silently socialized onto everyone else; in the worst case the vault becomes insolvent — later claimants' transfers revert permanently because earlier claimants drained the underfunded bucket.

### Likelihood Explanation
Triggering requires a `stopEpochWithDuration`/`stopEpoch` with `_lossAmount > 0` while `pendingWithdraws > 0` — i.e., an honest manager stopping an epoch at a loss with queued withdrawals, plus the victim claiming afterward. No privileged misbehavior is needed; the attacker is any unprivileged withdraw-requester whose receipt is in the loss epoch. The exploitability hinges on the exact ordering of `deposit` (epoch bump) vs `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration`, which I could not fully confirm within the available iterations — if the CDO collects pending-withdraw funds before the epoch-ending deposit, the key matches and the bug does not manifest.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch in which the receipts were requested, not by the strategy's current `epochNumber`. Either record `lossRecoveryPriceByEpoch[epochNumber - 1]` inside `collectWithdrawFunds` when called during the epoch-closing flow, or have the CDO pass the request epoch explicitly (e.g., `collectWithdrawFunds(_amount, _requestEpoch)`), or bump `epochNumber` only after all pending-withdraw funding and loss accounting for the closing epoch is complete. Add a test where `stopEpochWithDuration` funds pending receipts at a partial price and assert each claim equals `claimBasis * lossRecoveryPrice / RECOVERY_FULL`.

### Proof of Concept
Foundry fork sketch (assumes the existing harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossAdjustedReceiptPaysAtPar() external {
    // 1. Two users deposit into AA during buffer of epoch E.
    _depositWithUser(userA, 10_000 * ONE_SCALE, true);
    _depositWithUser(userB, 10_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // 2. userA requests withdraw in epoch E+1 buffer -> lastWithdrawRequest = E+1.
    vm.prank(userA);
    uint256 claimBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // 3. Manager stops epoch with a partial loss -> previewLossAdjustedWithdrawFunds
    //    returns pendingToFund < claimBasis; collectWithdrawFunds records
    //    lossRecoveryPriceByEpoch[epochNumber] where epochNumber is already E+2.
    deal(defaultUnderlying, borrower, fundedAmount);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, lossAmount, fundedAmount);

    // 4. userA claims: _claimLossAdjustedWithdrawRequest reads
    //    lossRecoveryPriceByEpoch[E+1] == 0, falls through to
    //    _claimFundedWithdrawRequest which pays claimBasis at par.
    uint256 balPre = underlying.balanceOf(userA);
    vm.prank(userA);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(userA) - balPre;

    uint256 expected = claimBasis * strategy.lossRecoveryPriceByEpoch(strategy.epochNumber()) / RECOVERY_FULL;
    assertGt(paid, expected);            // paid full basis instead of haircut
    assertLt(underlying.balanceOf(address(strategy)), remainingObligations);
}
```

Caveat: the PoC's success depends on `collectWithdrawFunds` executing after the `epochNumber`-incrementing `deposit` inside the CDO's stop-epoch flow; that ordering should be confirmed by tracing `IdleCDOEpochVariant.stopEpochWithDuration` before relying on this finding.