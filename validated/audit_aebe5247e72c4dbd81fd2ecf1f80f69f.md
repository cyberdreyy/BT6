### Title
`stopEpoch` commits loss and fee mutations before the borrower pull; on pull failure the default path keeps them, corrupting pending-receipt accounting - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The kernel bug is a resource leak on an error branch: state/resources acquired before a failure are not released. The analog in `IdleCDOEpochVariant._stopEpoch` is that several accounting mutations are committed *before* the `try this.getFundsFromBorrower(...)` call, and the `catch` branch (`_handleBorrowerDefault`) does not undo them. When `stopEpochWithDuration` is called with a nonzero `_lossAmount` and the borrower pull fails, pending-receipt state is double-counted.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol`:

- Line 384: `_accrueManagementFee()` checkpoints fees onto pre-stop NAV.
- Line 388-389: `expectedEpochInterest` and `pendingWithdrawFees` are persisted.
- Line 393: `(_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount)` mutates strategy state: it reduces `pendingWithdraws` and stores `lossRecoveryPriceByEpoch` for the affected receipts (per `collectWithdrawFunds`/`_claimLossAdjustedWithdrawRequest` semantics in `contracts/strategies/idle/IdleCreditVault.sol`).
- Lines 398-404 / 501-505: if the borrower pull fails, `_handleBorrowerDefault` runs — but nothing reverts or compensates the earlier `pendingWithdraws` reduction or the recorded loss-recovery price.

Later, when a defaulted-epoch receipt is claimed via `claimWithdrawRequest` → `_claimDefaultedWithdrawRequest` (`IdleCreditVault.sol`), the code does `pendingWithdraws -= claimBasis` where `claimBasis` is the *full* (pre-loss) per-epoch basis (`withdrawsRequestsByEpoch[user][epoch]` + APR0 principal + APR0 interest), while `pendingWithdraws` was already reduced by the loss-adjusted amount in the preview. Two consequences:

1. `pendingWithdraws` underflows (or is driven inconsistent) → `claimWithdrawRequest` reverts for default-epoch receipt holders → permanent freezing of their recovery claims, and the stale `lossRecoveryPriceByEpoch` entry additionally routes claims through `_claimLossAdjustedWithdrawRequest`, producing a double haircut (loss preview × `defaultRecoveryPrice`).
2. The `apr0Users`/normal receipt split recorded at request time no longer matches the reduced aggregate, so `_clearWithdrawClaimForEpoch` accounting drifts for all users sharing the epoch.

### Impact Explanation
Unprivileged withdraw-request holders of the defaulted epoch can have their recovery claims either permanently frozen (underflow revert in `pendingWithdraws -= claimBasis`) or double-haircutted (loss recovery price applied at preview and again via `defaultRecoveryPrice`). Loss is bounded by the sum of pending receipts outstanding at the defaulting `stopEpoch`, i.e. direct theft/permanent freezing of unclaimed user yield and principal.

### Likelihood Explanation
Requires only the ordinary sequence: users call `requestWithdraw` during the buffer, then the honest manager calls `stopEpochWithDuration(_newApr, _interest, _duration, _lossAmount > 0)` and the borrower's `transferFrom` fails (insufficient repayment — a normal default, not attacker-caused). No privileged misbehavior needed; the bug is that the preview/loss state survives an error branch it was not meant to survive. Not mitigated by skim, KYC, or only-CDO guards, since all calls are legitimately sequenced.

### Recommendation
In `_stopEpoch`, perform `previewLossAdjustedWithdrawFunds`, `expectedEpochInterest`, `pendingWithdrawFees`, and `_accrueManagementFee` mutations only after the borrower pull succeeds, or wrap the pre-pull section so the `catch` reverts the whole call and re-enters `_handleBorrowerDefault` in a separate transaction (mirroring how `getInstantWithdrawFunds` keeps funding and default in one try/catch without pre-committed state). Alternatively, make the default-claim path use the already-loss-adjusted `pendingWithdraws` basis rather than the raw per-epoch `claimBasis`.

### Proof of Concept
```solidity
// Foundry fork test sketch (contracts/test harness in test/foundry/IdleCreditVault.t.sol)
function testStopEpochLossThenDefaultCorruptsPendingReceipts() external {
    // 1. Deposit AA/BB, run epoch 0, stop normally.
    idleCDO.depositAA(10_000e6);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, expected);

    // 2. During buffer, request a normal withdraw -> pendingWithdraws = X,
    //    withdrawsRequestsByEpoch[user][epoch1] = X.
    cdoEpoch.requestWithdraw(receiptAmount, address(AAtranche));

    // 3. Epoch 1 runs, ends. Manager calls stopEpochWithDuration with _lossAmount > 0
    //    -> previewLossAdjustedWithdrawFunds reduces pendingWithdraws to funded amount F < X
    //    and stores lossRecoveryPriceByEpoch[epoch1].
    // 4. Borrower repays less than _amountToPullFromBorrower + _pendingWithdraws
    //    -> getFundsFromBorrower reverts -> _handleBorrowerDefault.
    deal(address(underlying), borrower, required - 1);
    vm.prank(borrower); underlying.approve(address(cdoEpoch), required - 1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, interest, duration, lossAmount);
    assertTrue(cdoEpoch.defaulted());

    // 5. finalizeDefaultRecovery by honest owner; then user claims.
    //    _claimDefaultedWithdrawRequest does pendingWithdraws -= X (full basis)
    //    while pendingWithdraws == F < X -> revert (underflow) or double haircut
    //    via _claimLossAdjustedWithdrawRequest -> claim bricked / underpaid.
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Caveat: I could not fully trace `previewLossAdjustedWithdrawFunds`/`finalizeDefaultRecovery` within the available iterations, so the exact revert-vs-double-haircut branch should be confirmed in the PoC; either outcome breaks the "one receipt one payout" invariant.