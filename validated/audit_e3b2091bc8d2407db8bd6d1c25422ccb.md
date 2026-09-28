### Title
Zero-rounded loss-recovery price reverts inside `_stopEpoch` try/catch and falsely triggers borrower default, freezing funded claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.collectWithdrawFunds` reverts with `NotAllowed` when a realized `stopEpochWithDuration` loss leaves the pending-receipt recovery price rounding to zero (`_amount * RECOVERY_FULL / pendingBasis == 0`). Because `IdleCDOEpochVariant._stopEpoch` calls `collectWithdrawFunds` *inside* the `try` around `getFundsFromBorrower`, this revert is swallowed by the `catch` and misinterpreted as the borrower failing to repay. An unprivileged lender who keeps a pending withdraw request large enough makes the zero-price condition reachable on any near-total honest loss, converting a clean loss accounting into a spurious `BorrowerDefault` that freezes all withdrawals until the owner runs the full default-recovery flow.

### Finding Description
The external bug (CVE-2019-16349) is a NULL-pointer dereference: a guarded/edge value reaches a code path whose caller assumes it cannot occur and crashes. The analog is a guarded zero-value edge whose caller mishandles it:

- `previewLossAdjustedWithdrawFunds` guarantees `pendingToFund >= 1` for `_lossAmount < totalBasis`, but `pendingToFund` can be arbitrarily small dust when the loss approaches `totalBasis` (`contracts/strategies/idle/IdleCreditVault.sol:454-459`).
- `collectWithdrawFunds` then computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` and reverts if it rounds to 0 (`IdleCreditVault.sol:417-419`). This needs only `pendingBasis > pendingToFund * 1e18`, e.g. `pendingToFund == 1` and a pending receipt above `1e18` units.
- In `IdleCDOEpochVariant._stopEpoch`, `_strategy.collectWithdrawFunds(_pendingWithdraws)` executes inside `try this.getFundsFromBorrower(...)` (`contracts/IdleCDOEpochVariant.sol:408-410`), and the `catch` unconditionally calls `_handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws)` (`IdleCDOEpochVariant.sol:501-505`).
- `_handleBorrowerDefault` sets `defaulted = true`, pauses the pool, stops the epoch, and disables AA/BB withdraw requests (`IdleCDOEpochVariant.sol:577-598`) — even though the borrower transferred every wei owed and `getFundsFromBorrower` itself succeeded.

So a revert meant as a sentinel guard is conflated with borrower insolvency. The broken invariant is the loss waterfall / epoch state machine: a fully-funded stop is recorded as a hard borrower default.

### Impact Explanation
Once `defaulted` is set, `claimWithdrawRequest` for already-funded receipts reverts via the epoch check (`epochNumber <= lastWithdrawRequest[_user]`, `IdleCreditVault.sol:326-328`), deposits are paused, and new withdraw requests are disabled. The pool can only be unwound through `finalizeDefault`/`finalizeDefaultRecovery`, which force all claimants through the recovery-price machinery (`defaultRecoveryPrice`, `defaultRecoveryReserve`, `postDefaultRequests`) even though the borrower paid in full. Result: temporary freezing of the entire pool's funds (every funded and pending claim, plus all active LP NAV) and misapplication of recovery accounting to a solvent borrower. Quantified loss: 100% of pool TVL is locked for the duration, plus any recovery-flow rounding/dust permanently isolated in `defaultRecoveryReserve`, and the attacker's own funded receipt becomes hostage to the same freeze.

### Likelihood Explanation
The trigger requires a near-total realized loss in `stopEpochWithDuration` (`_lossAmount` within dust of `activeBasis + pendingBasis`) — an extreme but honest manager input during a genuine catastrophic-loss event, exactly when correct accounting matters most. The attacker's contribution is cheap and fully unprivileged: deposit AA/BB and submit a withdraw request during the buffer so `pendingBasis` exceeds `pendingToFund * 1e18`; even an ordinary 1+ token pending receipt suffices when `pendingToFund` rounds to 1 wei. No privileged cooperation is needed; the manager/borrower behave honestly. Existing guards (`previewLossAdjustedWithdrawFunds` bounds, `defaultRecoveryInitialized`, `pendingWithdraws` accounting) do not prevent the zero-price rounding case — the guard that fires is the very mechanism of the bug.

### Recommendation
Do not let accounting reverts inside the funding `try` be treated as borrower insolvency. Either:
- compute `lossRecoveryPrice` before the `try` (e.g., in `previewLossAdjustedWithdrawFunds`, which is already called outside the try at `IdleCDOEpochVariant.sol:393`) and revert the whole `stopEpochWithDuration` with a distinct error instead of falling into `catch`; or
- in `collectWithdrawFunds`, clamp the funded amount to a minimum of 1 unit of recovery price (`if (lossRecoveryPrice == 0) lossRecoveryPrice = 1` only if `_amount != 0`, or distribute the dust remainder to active LPs) so a funded-but-dust recovery still records a non-zero `lossRecoveryPriceByEpoch` instead of reverting; or
- restructure the `try` so only the borrower `transferFrom` is inside it, moving `collectWithdrawFunds` and subsequent accounting outside, so strategy reverts propagate rather than fabricating a `BorrowerDefault`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against the deployed pool / repo harness (test/foundry/IdleCreditVault.t.sol style)
function test_NearTotalLossSpuriouslyDefaultsSolventBorrower() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Attacker: KYC-passing lender. Deposit during buffer and request a withdraw
    // so pendingBasis > 1e18 wei (> 1 underlying unit).
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, amount, true);            // AA deposit, wallet allowed
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));     // full-balance receipt
    uint256 pendingBasis = IdleCreditVault(address(strategy)).pendingWithdraws();
    assertGt(pendingBasis, 1e18, 'need pendingBasis > RECOVERY_FULL-scale dust');

    // Another honest LP keeps the pool alive.
    idleCDO.depositAA(amount);

    // Epoch runs; borrower repays everything owed. A real catastrophic loss occurs.
    _startEpochAndCheckPrices(0);
    uint256 duration = cdoEpoch.epochDuration();
    (uint256 pendingToFund, ) =
        strategy.previewLossAdjustedWithdrawFunds(0); // fetch structure
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees();
    uint256 totalBasis = activeBasis + pendingBasis;
    // Manager reports the real loss: everything but 1 wei.
    uint256 lossAmount = totalBasis - 1;
    (pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    // pendingToFund * 1e18 < pendingBasis  ->  lossRecoveryPrice rounds to 0.
    assertLt(pendingToFund * 1e18, pendingBasis, 'zero recovery price reachable');

    // Borrower is solvent and approves the full owed amount.
    uint256 owed = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(defaultUnderlying, borrower, owed, true);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), owed);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, duration, lossAmount);

    // BUG: collectWithdrawFunds reverted inside try -> catch -> _handleBorrowerDefault
    assertEq(cdoEpoch.defaulted(), true, 'solvent borrower wrongly marked defaulted');
    assertEq(cdoEpoch.isEpochRunning(), false);
    assertEq(cdoEpoch.allowAAWithdrawRequest(), false, 'requests frozen');

    // Funded / pending claims cannot be paid until the full default-recovery flow runs.
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();
}
```
The assertion `cdoEpoch.defaulted() == true` after a fully-funded stop demonstrates the misclassified state; `claimWithdrawRequest` reverting demonstrates the fund freeze. Recovery only via `finalizeDefault` + `finalizeDefaultRecovery` confirms the "temporary freezing of all pool funds" impact.