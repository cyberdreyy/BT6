### Title
A single dust APR0 withdraw request permanently prevents APR changes and bricks `stopEpoch`, freezing the pool - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The bug-class analog of "an owner config update retroactively re-locks already-earned tokens" maps to the APR0 withdraw-request lifecycle in `IdleCreditVault`: any unprivileged lender can open a dust withdraw request while `unscaledApr == 0`, which permanently poisons `apr0TotalPrincipal`. From then on, any honest manager APR increase makes `stopEpoch` revert unconditionally, freezing all pool funds and locking the vault at a 0% APR — i.e., unclaimed yield is "re-locked" (made unearnable) by a state transition the attacker controls, mirroring the vesting-config retroactive lock.

### Finding Description
`IdleCreditVault.requestWithdraw` routes any request made while `unscaledApr == 0` into `_requestWithdrawApr0`, which adds the requested principal to the global `apr0TotalPrincipal` bucket [1](#0-0) . This is reachable by any wallet allowed to call `IdleCDOEpochVariant.requestWithdraw` (a KYC-passing tranche holder), for an arbitrarily small amount.

`prepareStopEpochWithApr0`, invoked by `stopEpoch` on the CDO, reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` [2](#0-1) . The only cleanup of `apr0TotalPrincipal` happens inside that same function at the end of a successful stop [3](#0-2) , and per-user settlement only runs on claim, which the attacker simply never performs.

The existing guard is proven by the protocol's own test `testApr0InvariantRevertsIfAprChangesAfterApr0Request`, which shows `manager.setAprs(nonZero)` followed by `stopEpoch` reverting [4](#0-3) . The test treats this as an invariant check, but it creates an unprivileged griefing vector: the attacker needs no privilege to place the `apr0TotalPrincipal > 0` state that triggers the revert.

### Impact Explanation
Two quantifiable harms:

1. **Temporary/permanent freezing of all TVL**: once an APR0 request exists, if the honest manager ever sets `unscaledApr != 0` (the normal way to give lenders yield), every `stopEpoch` call reverts. The running epoch can never be closed, pending withdraws can never be funded, and deposits/claims are frozen until the APR is reverted to 0.
2. **Permanent suppression of yield**: the only recovery is to keep APR at 0. Because `requestWithdraw` re-creates the bucket each buffer period, the attacker can re-poison it every epoch with a dust balance, forcing the pool to run at 0% APR indefinitely — all lenders permanently forfeit their yield (theft of unclaimed yield / opportunity cost, the exact impact class of the source report).

### Likelihood Explanation
Likelihood is moderate: it requires the vault to be in (or pass through) an `unscaledApr == 0` configuration, which is a supported first-class mode (dedicated `Apr0UserData`, `apr0RateByEpoch`, `apr0TotalPrincipal` accounting exist). Once APR is 0 for any epoch, the attack costs only a dust deposit plus a `requestWithdraw` call. The attacker then either (a) forces the manager to keep APR at 0 forever, or (b) bricks `stopEpoch` the moment APR is raised. No existing guard prevents it: the loss-epoch revert in `requestWithdraw` only covers defaulted epochs [5](#0-4) , and KYC/allow-listing does not stop a whitelisted lender.

### Recommendation
Do not gate `stopEpoch` on `unscaledApr` being unchanged. Instead, snapshot the APR (and/or a flag) at the time each APR0 request is opened — analogous to the source report's "track how much has vested already" fix — and settle outstanding APR0 requests at the snapshotted terms, or simply pay out `apr0TotalPrincipal` without APR0 interest when the APR changes. Alternatively, allow the manager to force-settle/cancel APR0 buckets, or refuse APR0 withdraw requests once a non-zero APR epoch has been scheduled.

### Proof of Concept
```solidity
// Foundry, extending the existing IdleCreditVault.t.sol harness
function testApr0GriefBlocksAprChangeAndStopEpoch() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    // attacker deposits dust and is a normal allowed wallet
    uint256 dust = 1 * ONE_SCALE;
    uint256 minted = idleCDO.depositAA(dust);
    _transferBurnedTrancheTokens(address(this), true);

    // run epoch 0 normally at APR 0, then manager stops it
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    _forceLastEpochAprToZero(); // unscaledApr == 0 in buffer phase

    // ATTACK: dust withdraw request -> apr0TotalPrincipal > 0
    cdoEpoch.requestWithdraw(minted / 2, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    _startEpochAndCheckPrices(1);

    // honest manager tries to restore a normal APR for lenders
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, initialProvidedApr);

    // stopEpoch now ALWAYS reverts -> epoch cannot close -> TVL frozen
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // Only escape: APR back to 0. Attacker re-poisons next buffer period -> APR pinned at 0 forever.
}
```

Uncertainty note: I could not fully re-verify the exact `stopEpoch` call chain inside `IdleCDOEpochVariant.sol` (whether `prepareStopEpochWithApr0` is invoked unconditionally on every stop path, including `stopEpochWithDuration`/close-pool flows). The revert behavior itself is confirmed by the existing test; the PoC may need minor adjustment if some stop paths skip the APR0 preparation.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L263-270)
```text
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-294)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L505-508)
```text
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
```

**File:** test/foundry/IdleCreditVault.t.sol (L3306-3338)
```text
  function testApr0InvariantRevertsIfAprChangesAfterApr0Request() external {
    // Scenario: APR0 request is created, then APR is changed before settlement.
    // Expectation: stopEpoch reverts to enforce APR0 lifecycle invariant.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    _forceLastEpochAprToZero();

    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
  }
```
