### Title
Pending APR0 withdraw receipts permanently block `stopEpoch` once APR is raised, freezing all borrower funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can plant a non-zero `apr0TotalPrincipal` by submitting a withdraw request during a zero-APR buffer phase. Once the manager later configures a non-zero APR (a routine honest action when the pool's rate regime changes), every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0`, so the epoch can never be closed and borrower funds cannot be recalled. The only state that clears `apr0TotalPrincipal` is `stopEpoch` itself (line 540), creating a circular liveness deadlock.

### Finding Description
- `requestWithdraw` routes requests into the APR0 bucket whenever `unscaledApr == 0` and the pool is not closed: `_requestWithdrawApr0` increments `apr0TotalPrincipal` (IdleCreditVault.sol:285-286, 567-577).
- `prepareStopEpochWithApr0` is invoked unconditionally by `_stopEpoch` before any borrower pull (IdleCDOEpochVariant.sol:362). If `apr0TotalPrincipal != 0` and `unscaledApr != 0`, it reverts `NotAllowed` (IdleCreditVault.sol:499-508).
- `apr0TotalPrincipal` is only reset to zero inside `prepareStopEpochWithApr0` (line 540) — the same function that reverts. There is no admin escape hatch, no per-user cancellation, and no way to drain the bucket other than a successful stop.
- `setApr`/`setAprsWithBuffer` can be called by the manager (or the CDO via `_setScaledApr` during `stopEpochWithDuration`) at any time, including mid-epoch or during the buffer, so an honest rate change is enough to arm the trap (IdleCreditVault.sol:217-235).

Attacker sequence:
1. Pool operates in APR0 mode (`unscaledApr == 0`) during a buffer phase.
2. Attacker (KYC-passed lender) deposits AA and calls `requestWithdraw` — `apr0TotalPrincipal` becomes non-zero.
3. Manager honestly sets a non-zero APR for the coming epoch via `setAprs`/`setAprsWithBuffer` (or `stopEpochWithDuration` sets `_newApr != 0` for the *next* epoch while the attacker's second APR0 request is pending in the next buffer... concretely: any state where a stop is attempted while `unscaledApr != 0 && apr0TotalPrincipal != 0`).
4. Every `stopEpoch` call reverts inside the strategy before `getFundsFromBorrower` runs — no default is declared (the revert happens before the try/catch), `isEpochRunning` stays true, and borrower principal + interest are locked.

The broken invariant is liveness/solvency: the epoch state machine can be wedged by unprivileged state (`apr0TotalPrincipal`) that cannot be cleared without the very call it blocks.

### Impact Explanation
All funds lent to the borrower for the running epoch (potentially the entire pool TVL) become un-recallable while the inconsistency persists. Withdrawals, interest settlement, loss realization, and default finalization are all gated behind a successful `stopEpoch`, so pending receipts and active LP NAV are frozen. The freeze is temporary only in the narrow sense that the manager can revert APR to 0 and stop — but if the pool has legitimately transitioned to a non-zero rate regime, this forces a rate-policy rollback dictated by an attacker's dust-sized APR0 receipt, and until resolved the borrower retains all funds.

### Likelihood Explanation
Requires: (a) a period where `unscaledApr == 0` (a supported, tested mode), (b) any single lender requesting withdrawal during that window — economically free, and (c) a subsequent honest APR increase, which is the natural configuration change after a zero-rate epoch. No privileged misbehavior, oracle manipulation, or timing precision is needed; the attacker only needs to hold one open APR0 request when the rate changes. The revert-by-design guard confirms the developers anticipated the inconsistent state but chose a revert rather than a safe settlement path.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0`. Instead, settle the APR0 bucket safely: either (a) treat the open APR0 principal as settling at zero rate for the epoch (emit the bucket into `apr0RateByEpoch[epochNumber] = 0` and clear `apr0TotalPrincipal`), letting claimants recover principal via `_settleApr0`, or (b) compute their pro-rata interest under the applicable rate. Alternatively, gate `setApr`/`setAprsWithBuffer` to reject a non-zero APR while `apr0TotalPrincipal != 0`, forcing the epoch to stop first — this keeps the invariant "APR0 principal only exists while APR is 0" at the entry point rather than at the liveness-critical stop path.

### Proof of Concept
Foundry fork PoC (sketch, against the existing `IdleCreditVault.t.sol` harness):

```solidity
function testApr0ReceiptBlocksStopAfterAprIncrease() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // Pool in APR0 mode
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // Attacker deposits and requests withdraw during buffer
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Honest manager raises APR for the next epoch before starting/stopping
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18); // unscaledApr != 0

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest()
        + IdleCreditVault(address(strategy)).pendingWithdraws());

    // stopEpoch reverts inside prepareStopEpochWithApr0 -> epoch can never close
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // Still running; borrower funds are not recalled; no default triggered
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
}
```

Key evidence: revert at `IdleCreditVault.sol:506-508`, sole reset of `apr0TotalPrincipal` at `IdleCreditVault.sol:540` inside the same reverting function, unconditional call at `IdleCDOEpochVariant.sol:362` before the borrower try/catch at `IdleCDOEpochVariant.sol:408`.