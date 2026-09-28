### Title
Mid-epoch APR changes reprice new deposits and create unapproved borrower debt - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.setAprs()` and `setApr()` can update the vault APR while an epoch is running. A KYC-approved lender can monitor an honest manager’s APR increase and immediately call `depositDuringEpoch()`. The deposit is priced using the newly increased APR, is transferred directly to the borrower, and increases the borrower’s `expectedEpochInterest` repayment obligation without a borrower confirmation. The attacker receives the enlarged fixed-rate return when the manager stops the epoch.

### Finding Description
`IdleCreditVault.setAprs()` writes `unscaledApr` and calls `setApr()`, while `setApr()` permits calls from the CDO or `manager` but does not reject calls while `IdleCDOEpochVariant.isEpochRunning` is true. [1](#0-0) 

`depositDuringEpoch()` is executable while `isEpochRunning` is true when the owner has enabled mid-epoch deposits. It computes the deposit’s borrower-funded interest from the *current* strategy APR through `_calcInterest(_amount)`, adds that value to `expectedEpochInterest`, mints strategy tokens, and transfers the deposited underlying directly to the borrower. [2](#0-1) [3](#0-2) 

The APR read is dynamic: `_calcInterest()` loads `_getStrategyApr()` at transaction execution rather than using the APR locked by `startEpoch()`. [4](#0-3) 

Consequently, an APR update intended by the honest manager for later lending terms immediately reprices a new mid-epoch deposit. Existing deposits retain the interest already accumulated in `expectedEpochInterest`, while only the attacker’s new deposit receives the increased rate.

### Impact Explanation
The borrower suffers a direct unexpected repayment increase. For example, with a 36.5-day epoch, 5-day buffer, and a halfway deposit of 1,000 USDC, increasing APR from 10% to 100% raises that deposit’s calculated interest from approximately 6.37 USDC to 63.70 USDC. The attacker extracts approximately 57.33 USDC of additional borrower-funded interest for the remaining epoch plus buffer.

The borrower did not authorize the changed terms for that specific deposit. `depositDuringEpoch()` transfers the principal to the borrower and increases the epoch repayment accounting atomically, so the borrower cannot reject the new obligation.

### Likelihood Explanation
Likelihood is moderate. The sequence requires an honest manager to call `setApr()` or `setAprs()` during an active epoch and mid-epoch deposits to be enabled. Both are ordinary protocol operations, and the missing epoch-state guard permits the sequence without privileged misconduct. Any KYC-passing lender can atomically deposit after observing the APR update.

The issue does not affect epochs where `isDepositDuringEpochDisabled` remains true, AYS remains enabled, or programmable-borrower mode is active.

### Recommendation
Lock the APR used for the active epoch:

- Store the scaled APR in `IdleCDOEpochVariant` during `startEpoch()`, or
- Add an `isEpochRunning` guard to `IdleCreditVault.setApr()`/`setAprs()` for manager-initiated changes, while preserving the CDO’s call at `stopEpoch()`, or
- Add an explicit borrower-consent mechanism before a mid-epoch deposit can increase `expectedEpochInterest`.

If APR changes are intended for the next epoch, write them to a pending-APR field and activate them only in `stopEpoch()`.

### Proof of Concept
Add this test to `TestIdleCreditVault` in `test/foundry/IdleCreditVault.t.sol`. It uses the existing mainnet-fork setup and shows the same mid-epoch deposit receiving materially more borrower-funded interest after an APR update.

```solidity
function testMidEpochDepositUsesMutableApr() external {
  // One 36.5-day epoch with a 5-day buffer and no fees.
  vm.prank(owner);
  cdoEpoch.setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);
  vm.prank(owner);
  cdoEpoch.setIsAYSActive(false);

  idleCDO.depositAA(1_000 * ONE_SCALE);

  vm.prank(manager);
  cdoEpoch.startEpoch();

  // Half of the epoch has elapsed.
  vm.warp(cdoEpoch.epochEndDate() - cdoEpoch.epochDuration() / 2);

  vm.prank(owner);
  cdoEpoch.setIsDepositDuringEpochDisabled(false);

  // Honest manager raises APR while the epoch is live.
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprs(
    100e18,
    _scaleAprWithBuffer(100e18)
  );

  address attacker = makeAddr('kycLender');
  uint256 amount = 1_000 * ONE_SCALE;
  deal(defaultUnderlying, attacker, amount);

  uint256 expectedBefore = cdoEpoch.expectedEpochInterest();
  uint256 borrowerBefore = underlying.balanceOf(borrower);

  vm.startPrank(attacker);
  underlying.approve(address(cdoEpoch), amount);
  uint256 minted = cdoEpoch.depositDuringEpoch(amount, address(AAtranche));
  vm.stopPrank();

  uint256 increasedInterest = cdoEpoch.expectedEpochInterest() - expectedBefore;

  // With the original 10% APR this halfway deposit would accrue ~6.37 USDC.
  // After the live APR update it accrues ~63.70 USDC.
  assertGt(increasedInterest, 60 * ONE_SCALE);
  assertEq(underlying.balanceOf(borrower) - borrowerBefore, amount);

  // Fund the honest borrower's enlarged repayment and settle the epoch.
  deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(10e18, 0);

  uint256 attackerValue = minted * cdoEpoch.virtualPrice(address(AAtranche)) / ONE_TRANCHE;
  assertApproxEqAbs(attackerValue, amount + increasedInterest, 2);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-235)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

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
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-668)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
```

**File:** contracts/IdleCDOEpochVariant.sol (L691-732)
```text
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);

    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L798-808)
```text
  /// @notice Calculate the interest of an epoch for the given amount
  /// @param _amount Amount of underlyings
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
```
