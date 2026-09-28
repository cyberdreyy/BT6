### Title
Permanent revert of `stopEpoch` when APR changes while a dust APR0 withdraw request is open - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts if `unscaledApr != 0` while `apr0TotalPrincipal > 0`. The invariant "an APR0 request lifecycle must run entirely under APR = 0" is only checked at epoch settlement time, not when the APR is changed and not when the request is created. A lender can open a minimal APR0 withdraw request during a 0-APR epoch; once the APR is set to a non-zero value for the next epoch (a perfectly normal, honest manager action at `startEpoch`/`stopEpochWithDuration`/`setAprs`), every subsequent `stopEpoch` reverts until the manager manually forces the APR back to 0 and settles. This mirrors the nouns-builder bug where `_computeTotalRewards` enforces a sum invariant at settlement time that was never enforced at set time.

### Finding Description
In `IdleCreditVault.sol`:

```solidity
// prepareStopEpochWithApr0 (lines ~499-508)
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`apr0TotalPrincipal` is incremented in `_requestWithdrawApr0` whenever a user calls `requestWithdraw` while `unscaledApr == 0` (line 285-286, 567-577). It is only cleared inside `prepareStopEpochWithApr0` (line 540, `apr0TotalPrincipal = 0`) — which is unreachable once the `unscaledApr != 0` revert fires — or in `_clearWithdrawClaimForEpoch` on the default-finalization path only.

`unscaledApr` is set by `setAprs`/`setAprsWithBuffer` (lines 206-220) with no check that an open APR0 bucket exists, and by `_setScaledApr` at epoch transitions. There is no check at request time that the APR will remain 0, and no check at APR-set time that `apr0TotalPrincipal == 0`. The check lives exclusively in the settlement path invoked by `IdleCDOEpochVariant._stopEpoch` (line 362).

### Impact Explanation
Broken invariant: liveness of the epoch state machine. `stopEpoch`/`stopEpochWithDuration` always reverts while `apr0TotalPrincipal != 0 && unscaledApr != 0`. Because `epochNumber` is only bumped inside a successful `stopEpoch` (`deposit()` at line 610), APR0 claimants cannot claim either — `_settleApr0` requires `principalEpoch < epochNumber`. All user funds (deposits + pending withdraw receipts) are frozen for the duration of the DoS. The freeze is temporary rather than permanent only because an honest manager can still call `setAprs(0, 0)` to restore `unscaledApr == 0` and settle — identical severity profile to the external report (temporary DoS, resolved by parameter change), with the difference that the attacker here is an unprivileged lender who only needs a dust withdraw request.

### Likelihood Explanation
- Trigger requires only: (1) an epoch running/pending under `unscaledApr == 0` (APR0 mode is a supported configuration, exercised by tests), (2) any KYC-passing lender calls `requestWithdraw` with any amount ≥ 1 wei, (3) the manager later sets a non-zero APR for a subsequent epoch — a routine operational action.
- No privileged misbehavior is needed; the attacker transaction is a standard withdraw request in the buffer/running phase.
- Existing guards do not prevent it: `requestWithdraw` does not check future APR intent; `setAprs`/`setAprsWithBuffer`/`setApr` (lines 206-235) only validate `maxApr`, never `apr0TotalPrincipal`; the existing test `testApr0InvariantRevertsIfAprChangesAfterApr0Request` confirms the revert fires exactly in this sequence.

### Recommendation
Move the invariant to set time, as in the external recommendation:
- Revert in `setApr`/`setAprs`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0 && _unscaledApr != 0`, so the APR can never be changed while an open APR0 bucket exists; and/or
- Settle/clear the open APR0 principal into `settledPrincipal` at APR-change time instead of reverting at `stopEpoch`, so settlement remains unblocked.

### Proof of Concept
Foundry fork test (mainnet fork, existing `IdleCreditVault.t.sol` harness style):

```solidity
function testApr0DustRequestDoSStopEpoch() external {
    // zero performance fee, AYS off — mirrors existing APR0 tests
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // run and stop an epoch at APR 0
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _forceLastEpochAprToZero(); // unscaledApr == 0, APR0 mode active

    // ATTACKER: any KYC'd lender opens a dust APR0 withdraw request
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, 1, true); // 1 wei AA deposit
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // apr0TotalPrincipal = 1

    // HONEST manager starts the next epoch with a normal non-zero APR
    _startEpochAndCheckPrices(1); // sets unscaledApr != 0

    // settle: stopEpoch permanently reverts while the dust bucket is open
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialApr, 0);

    // attacker cannot self-unstick: claim also reverts (epochNumber not bumped)
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // only recovery: manager must force APR back to 0 and settle at 0
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // now succeeds
}
```