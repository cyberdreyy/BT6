### Title
Dust APR0 withdraw request permanently reverts `stopEpoch` once APR becomes non-zero, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the reported `poke()` revert-on-dust bug, `IdleCreditVault.prepareStopEpochWithApr0` hard-reverts when a global APR0 principal bucket is non-zero while `unscaledApr != 0`. An unprivileged lender can create this poisoned state with a minimal `requestWithdraw` during an APR=0 epoch; if the manager later raises the APR, every subsequent `stopEpoch`/`stopEpochWithDuration` reverts, so the epoch can never close and all user funds remain locked until privileged remediation.

### Finding Description
`requestWithdraw` routes requests into the APR0 bucket whenever `unscaledApr == 0`:

```solidity
if (unscaledApr == 0 && !isClosed) {
  _requestWithdrawApr0(_amount, _user);
}
```

`_requestWithdrawApr0` increments the global `apr0TotalPrincipal` (IdleCreditVault.sol:567-577). That bucket is only ever cleared inside `prepareStopEpochWithApr0` (`apr0TotalPrincipal = 0`, line 540) or on the default-claim path — a user claiming their withdraw via `_settleApr0` does NOT decrement `apr0TotalPrincipal`, so the bucket cannot be drained by user actions.

During `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` calls `prepareStopEpochWithApr0` (IdleCDOEpochVariant.sol:362), which contains:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

Attack sequence:

1. Pool runs (or buffers) an epoch with `unscaledApr == 0` (e.g., launch config or an APR0 epoch).
2. Attacker (any KYC-passing lender) calls `IdleCDOEpochVariant.requestWithdraw` with a dust tranche amount, producing a small `_amount` that lands in `apr0Users[attacker].principal` and `apr0TotalPrincipal`.
3. Manager sets a non-zero APR via `setAprs`/`setAprsWithBuffer`/`setApr` (a routine, honest configuration change).
4. `epochEndDate` passes; manager calls `stopEpoch`. `prepareStopEpochWithApr0` sees `_principal != 0` and `unscaledApr != 0` → `revert NotAllowed()`.
5. The attacker cannot remove the poison: claiming requires `epochNumber` to advance (which only happens inside the reverting `stopEpoch`), and `_settleApr0` never touches `apr0TotalPrincipal` anyway. The default-claim path that decrements it requires `defaultRecoveryFinalized`.
6. Every `stopEpoch`/`stopEpochWithDuration` reverts until the manager sets APR back to 0, stops the epoch (clearing the bucket), then re-raises APR.

This mirrors the original bug: a dust-sized, user-controlled entry makes an epoch-closing keeper function revert instead of skipping the negligible amount, keeping stale state alive.

### Impact Explanation
Temporary freezing of the entire pool: while the revert persists, the epoch cannot close, pending withdraw requests cannot be funded or claimed, queued withdrawals/deposits cannot be processed, and all lender principal stays locked in the borrower allocation. The locked amount equals the full pool TVL for the duration of the freeze. Recovery requires a privileged APR reset (set `unscaledApr` back to 0, complete one `stopEpoch`, then re-raise), so the freeze duration depends on manager detection and remediation.

### Likelihood Explanation
- Attacker cost is minimal: a single dust `requestWithdraw` during any APR=0 epoch; requests are open to any allowed lender via `requestWithdraw` when `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are set.
- Trigger condition requires the manager to raise APR while the APR0 bucket is pending — plausible since APR0 periods are transitional by design (a vault that later charges interest must at some point set APR > 0).
- Not permanent (recoverable via APR reset), and requires an honest-manager action as a precursor, which bounds it to Medium rather than High.

### Recommendation
Do not gate the whole epoch close on the APR0 bucket state. Either skip settling the stale bucket when `unscaledApr != 0` (treat its accrued interest as 0 and clear/rollover `apr0TotalPrincipal`), or clear the bucket at claim/settle time so users can always drain it. Concretely, replace the revert in `prepareStopEpochWithApr0` with a path that finalizes APR0 principal at zero rate when APR was raised mid-lifecycle, or decrement `apr0TotalPrincipal` inside `_settleApr0`/`_claimFundedWithdrawRequest` so the bucket is user-clearable.

### Proof of Concept
```solidity
function testApr0DustBlocksStopEpoch() external {
    // setup: pool running with unscaledApr == 0
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // APR already 0 at setup
    assertEq(IdleCreditVault(address(strategy)).unscaledApr(), 0);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    // stop epoch 0 normally (apr still 0)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // buffer period, APR still 0: attacker files a dust APR0 withdraw request
    address attacker = makeAddr('attacker');
    uint256 dust = (ONE_TRANCHE + cdoEpoch.virtualPrice(address(AAtranche)) - 1)
        / cdoEpoch.virtualPrice(address(AAtranche));
    deal(address(AAtranche), attacker, dust);
    vm.startPrank(attacker);
    AAtranche.approve(address(cdoEpoch), dust);
    cdoEpoch.requestWithdraw(dust, address(AAtranche));
    vm.stopPrank();
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // manager raises APR for next epoch (honest action)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // stopEpoch now always reverts: apr0TotalPrincipal != 0 && unscaledApr != 0
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    deal(defaultUnderlying, borrower, expectedInterest + pending);
    vm.prank(borrower);
    IERC20(defaultUnderlying).approve(address(cdoEpoch), expectedInterest + pending);

    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // attacker cannot unbrick it: claim still gated on epochNumber advancing
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
}
```

Note on uncertainty: I verified the revert and the absence of a user-side clearing path for `apr0TotalPrincipal` in the excerpts read; the claim-gating revert in `_claimFundedWithdrawRequest` (line 326-328) confirms users cannot settle before `epochNumber` advances, but I did not exhaustively trace every `IdleCDOEpochVariant.requestWithdraw` admission flag (e.g., whether a request can be filed while `isEpochRunning` vs only in buffer). The dust-request path during the buffer period is the standard flow shown in the test suite, so the PoC uses that window.