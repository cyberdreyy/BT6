### Title
Changing APR while an APR0 withdrawal is pending temporarily freezes `stopEpoch` - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
A withdrawal requested while `unscaledApr == 0` is stored in the APR0 accounting bucket via `_requestWithdrawApr0` and increments `apr0TotalPrincipal` [1](#0-0) . `prepareStopEpochWithApr0` later reverts whenever that bucket is non-zero and `unscaledApr` has become non-zero [2](#0-1) . Because `stopEpoch` calls this before its borrower-transfer `try/catch`, the revert cannot be converted into a default and blocks the epoch transition [3](#0-2) .

### Finding Description
`IdleCDOEpochVariant.requestWithdraw` records a normal withdrawal through `IdleCreditVault.requestWithdraw` when instant withdrawals are disabled or the APR-drop condition is not met [4](#0-3) . The strategy chooses APR0 accounting solely from the current `unscaledApr == 0` value [5](#0-4) .

The manager can subsequently update both `unscaledApr` and the scaled APR directly through `setAprsWithBuffer`; this setter has no check for an open APR0 bucket or epoch state [6](#0-5) . Once `unscaledApr != 0`, the next `stopEpoch` always reverts until APR is changed back to zero because `apr0TotalPrincipal` remains open [7](#0-6) .

This is the same lifecycle mismatch as the reference issue: a user-created object is valid under one mode, but a later mode/configuration change leaves epoch processing unable to handle it.

### Impact Explanation
A KYC-passing lender can leave a minimal APR0 withdrawal request outstanding and cause a subsequent legitimate APR update to make `stopEpoch` unusable. The impact is temporary freezing of the entire vault’s epoch settlement: deposits, withdrawals, borrower repayment processing, and all other pending claims remain locked until the manager restores `unscaledApr` to zero. Recovery requires only an additional privileged transaction, so this does not create permanent insolvency or direct theft.

The amount at risk for temporary freezing is the vault’s active TVL plus pending withdrawal liabilities, while the attacker only needs enough tranche balance to create a non-zero `apr0TotalPrincipal`.

### Likelihood Explanation
The sequence requires an APR0 epoch and a later APR change while an APR0 receipt is pending. Both states are explicitly supported: APR0 requests receive dedicated accounting, and the manager is allowed to call `setAprsWithBuffer` directly [6](#0-5) . The likelihood is limited by the required APR0 operating mode and privileged APR update, but no malicious privileged role is required.

### Recommendation
Prevent `setApr`, `setAprs`, and `setAprsWithBuffer` from changing `unscaledApr` away from zero while `apr0TotalPrincipal != 0`, or settle/close the APR0 bucket before applying the new APR. Alternatively, persist the request’s APR regime and let `prepareStopEpochWithApr0` process APR0 receipts independently of the strategy’s current APR.

### Proof of Concept
Add this test to the existing `test/foundry/IdleCreditVault.t.sol` fixture, where `idleCDO`, `cdoEpoch`, `strategy`, `AAtranche`, `manager`, and the epoch helpers are already initialized.

```solidity
function testApr0RequestBlocksStopAfterAprChange() external {
  uint256 amount = 10_000 * ONE_SCALE;

  // Keep the request on the normal/APR0 path rather than the instant path.
  vm.prank(manager);
  cdoEpoch.setInstantWithdrawParams(
    cdoEpoch.instantWithdrawDelay(),
    cdoEpoch.instantWithdrawAprDelta(),
    true
  );

  idleCDO.depositAA(amount);

  // Run one epoch, then stop it with next-epoch APR = 0.
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch());
  assertEq(strategy.unscaledApr(), 0);

  // Attacker/lender opens an APR0 withdrawal during the buffer period.
  uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertGt(strategy.apr0TotalPrincipal(), 0);
  assertGt(strategy.pendingWithdraws(), 0);
  assertGt(requested, 0);

  // Start the zero-APR epoch carrying the pending APR0 receipt.
  _startEpochAndCheckPrices(0);

  // A legitimate manager APR update changes the mode while APR0 principal is open.
  vm.prank(manager);
  strategy.setAprsWithBuffer(
    1e18,
    cdoEpoch.epochDuration(),
    cdoEpoch.bufferPeriod()
  );
  assertGt(strategy.unscaledApr(), 0);

  // The prepareStopEpochWithApr0 guard reverts before the borrower try/catch.
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  cdoEpoch.stopEpoch(0, 0);

  // Restoring APR0 allows settlement, confirming temporary freezing rather than theft.
  vm.prank(manager);
  strategy.setAprsWithBuffer(
    0,
    cdoEpoch.epochDuration(),
    cdoEpoch.bufferPeriod()
  );

  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L212-225)
```text
  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
  function setApr(uint256 _apr) public {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L497-508)
```text
    // Principal currently waiting for withdraw that was requested while APR was 0,
    // net of the upfront management fee charged at request time.
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L357-364)
```text
    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-790)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```
