### Title
Dust APR0 withdraw request permanently bricks `stopEpoch` if APR later becomes non-zero — total vault freeze (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0()` enforces the invariant that APR must remain 0 for the whole lifecycle of an APR0 withdraw request by reverting when `unscaledApr != 0` while `apr0TotalPrincipal != 0`. Because `apr0TotalPrincipal` has no other clearing path reachable before this check, a single dust-sized APR0 withdraw request turns any subsequent honest APR change into a permanent, unrecoverable revert of `stopEpoch`, freezing the entire vault.

### Finding Description
- During a buffer/epoch where `unscaledApr == 0`, any KYC-passed tranche holder can call `requestWithdraw` on the CDO, which routes to `IdleCreditVault.requestWithdraw` and, since `unscaledApr == 0`, increments the global `apr0TotalPrincipal` via `_requestWithdrawApr0` (IdleCreditVault.sol:285-286, 567-577). There is no minimum amount — 1 wei suffices.
- If the (honest) manager later raises the APR via `setAprs`/`setAprsWithBuffer`, or a `stopEpoch` sets a non-zero next APR, `unscaledApr` becomes non-zero.
- On the next `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` calls `_strategy.prepareStopEpochWithApr0(_interest)` at IdleCDOEpochVariant.sol:362 — *before* the `try` block that handles borrower default. Inside, the check `if (unscaledApr != 0) revert NotAllowed()` at IdleCreditVault.sol:506-508 fires whenever `apr0TotalPrincipal != 0`.
- The revert propagates out of `stopEpoch` entirely (it is not the caught borrower-default path), so `epochNumber` never bumps, funds are never recalled, and `defaulted` is never set.
- Escape analysis: `apr0TotalPrincipal` is only cleared at IdleCreditVault.sol:540 (unreachable — it's after the reverting check, and the revert rolls back the write anyway) or decremented in `_clearWithdrawClaimForEpoch` with `_isClearingApr0 = true` (IdleCreditVault.sol:827), which is only reachable via `_claimDefaultedWithdrawRequest` after `defaultRecoveryFinalized` — which itself requires a `stopEpoch`-driven default that can never occur. `_settleApr0` and `_claimFundedWithdrawRequest` move/delete user-level APR0 data but never decrement `apr0TotalPrincipal`, so user-side claims cannot drain it either. The vault is permanently frozen short of an implementation upgrade.

### Impact Explanation
Permanent freezing of all user funds. Once bricked, no epoch can ever be stopped: borrower funds cannot be recalled, no withdraw requests or claims can be processed, deposits are stuck, and the default/recovery flow (`_handleBorrowerDefault`, `finalizeDefault`, `DefaultDistributor`) is unreachable because it is gated on `stopEpoch` executing. Loss equals the full pool TVL plus pending withdraw receipts — analogous to the reference bug's crash, but here the crash becomes a permanent state rather than a one-shot DoS.

### Likelihood Explanation
Requires a pool operating at `unscaledApr == 0` (an explicitly supported mode exercised by the test suite, e.g. `_forceLastEpochAprToZero`, and reachable when the manager sets APR 0 for wind-down or zero-yield periods). During any such window a single unprivileged lender locks a dust receipt. The triggering second step is a routine, honest manager action (restoring a positive APR), so the attack cost is one `requestWithdraw` of 1 wei. The revert itself is intentional (see `testApr0InvariantRevertsIfAprChangesAfterApr0Request`), but the test only asserts the revert — it does not acknowledge that the invariant failure is permanent and unrecoverable, which is the actual vulnerability.

### Recommendation
Do not use a hard revert as the APR0 lifecycle guard. Options: (a) block APR changes while `apr0TotalPrincipal != 0` — move the check into `setApr`/`setAprsWithBuffer` so the state can never become inconsistent; (b) allow users/keeper to cancel an APR0 request and decrement `apr0TotalPrincipal` so the bucket can be drained; (c) on a non-zero-APR `stopEpoch`, settle APR0 receipts at zero interest and clear the bucket instead of reverting. Any of these preserves the invariant without making it a permanent vault freeze.

### Proof of Concept
Foundry fork test sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0DustRequestPermanentlyBricksStopEpoch() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    _startEpochAndCheckPrices(0);
    // end epoch 0 with next-epoch APR = 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _forceLastEpochAprToZero();

    // attacker: dust APR0 withdraw request during buffer
    cdoEpoch.requestWithdraw(1, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    _startEpochAndCheckPrices(1);

    // honest manager restores a positive APR mid-epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    // every stopEpoch now reverts — permanently
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // no cleanup path: even direct requests/claims cannot clear apr0TotalPrincipal
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpochWithDuration(0, 0, 30 days, 0);
}
```

Uncertainty note: I verified the clearing paths for `apr0TotalPrincipal` visible in `IdleCreditVault.sol`, but did not exhaustively trace `IdleCDO._emergencyShutdown`/`restoreOperations` for a privileged escape; even if one exists, it would not mitigate the freeze of borrower-held funds without an upgrade.