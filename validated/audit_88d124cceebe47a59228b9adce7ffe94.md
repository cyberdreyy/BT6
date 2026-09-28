### Title
Broken programmable-borrower vault permanently prevents default and freezes LP funds - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
`IdleCDOEpochVariant.stopEpoch()` cannot fall back to `_handleBorrowerDefault()` when the configured `ProgrammableBorrower`’s ERC4626 vault makes `convertToAssets()` or `withdraw()` permanently revert. Because the vault valuation is required before the guarded `try` that handles borrower-default settlement, one corrupted external vault can permanently freeze the epoch and all funds entrusted to the programmable borrower. [1](#0-0) [2](#0-1) 

### Finding Description
`stopEpoch()` resolves the programmable borrower’s interest before calling the borrower hook, and `ProgrammableBorrower.totalInterestDueNow()` depends on `_vaultNetInterest()`, `_currentVaultAssets()`, and finally `vault.convertToAssets()`. [3](#0-2) [2](#0-1)  A permanently reverting ERC4626 implementation, proxy, oracle dependency, or destroyed vault causes this call chain to revert before `stopEpoch()` reaches `onStopEpoch()` or its `try this.getFundsFromBorrower(...)` / `catch` default path. [4](#0-3) 

Even if the valuation call were bypassed, `onStopEpoch()` calls `_currentVaultAssets()` to determine whether a withdrawal failure is economically covered and then calls `vault.withdraw()`. [5](#0-4)  `ProgrammableBorrower.onDefault()` intentionally avoids vault valuation, but the protocol has no independent owner or manager entrypoint that invokes it; it is called only after `stopEpoch()` reaches `_handleBorrowerDefault()`. [6](#0-5) [7](#0-6) 

### Impact Explanation
This produces permanent freezing of active tranche principal, pending withdrawal receipts, and borrower-held or vault-held assets. `isEpochRunning` remains true, `defaulted` remains false, withdrawals cannot be requested during the active epoch, and `restoreOperations()` returns without unpausing while an epoch is active. [8](#0-7)  The entire programmable-borrower exposure is affected, rather than only the unavailable vault share position.

The owner-side escape hatches do not reliably recover the position: `emergencyExitVault()` still calls `vault.redeem()`, while `setVault()` refuses to switch while epoch accounting is active or old vault shares remain. [9](#0-8) [10](#0-9)  `rescueTokens()` can move only ERC20 balances held directly by the borrower adapter and cannot extract shares trapped in the broken vault. [11](#0-10) 

### Likelihood Explanation
The issue requires an external ERC4626 integration to enter a state where valuation or withdrawal permanently reverts, such as a failed implementation upgrade, deleted implementation code, or permanently broken pricing dependency. This is less likely than an ordinary borrower default, but the protocol explicitly treats vault withdrawal failure as a possible runtime condition and only makes transient failures retryable. [12](#0-11)  No unprivileged protocol role is required to trigger the external failure, and honest privileged callers cannot force the already-marked epoch into default once the view itself reverts.

### Recommendation
Isolate programmable-borrower valuation and vault liquidity failures inside `stopEpoch()` so they deterministically enter `_handleBorrowerDefault()` instead of reverting before the default path. In particular:

- Call the borrower hook through a self-call or otherwise catch reverts from `totalInterestDueNow()`, `onStopEpoch()`, `convertToAssets()`, and `vault.withdraw()`.
- Add a guarded owner/manager `defaultEpoch()` path that calls `IProgrammableBorrower.onDefault()` without requiring a live vault valuation.
- Allow `ProgrammableBorrower.setVault()` or an explicit migration path after a CDO-recorded default, while preserving an accounting record of stranded old-vault shares.
- Keep `onDefault()` free of calls to `convertToAssets()` and avoid assuming `vault.redeem()` is executable during recovery.

### Proof of Concept
A Foundry fork test can reproduce the permanent freeze by making the configured ERC4626 vault’s valuation and redemption interfaces revert during a running programmable-borrower epoch:

```solidity
function testBrokenVaultCannotDefaultEpoch() external {
    // Existing fixture state:
    // - cdo is IdleCDOEpochVariant with isProgrammableBorrower = true
    // - programmableBorrower is configured as borrower adapter
    // - epoch is running and programmableBorrower has nonzero vault shares
    vm.prank(manager);
    cdo.startEpoch();

    vm.warp(cdo.epochEndDate() + 1);

    // Simulate the external ERC4626 implementation being destroyed or upgraded
    // into code whose convertToAssets/withdraw/redeem calls always revert.
    vm.etch(programmableBorrower.vault(), hex"");
    assertGt(programmableBorrower.vaultSharesBalance(), 0);

    // The intended default path is unreachable: totalInterestDueNow() /
    // _currentVaultAssets() reverts before stopEpoch reaches its try/catch.
    vm.prank(manager);
    vm.expectRevert();
    cdo.stopEpoch(newApr, 0);

    assertTrue(cdo.isEpochRunning());
    assertFalse(cdo.defaulted());
    assertTrue(cdo.paused());
    assertFalse(cdo.allowAAWithdrawRequest());
    assertFalse(cdo.allowBBWithdrawRequest());

    // Owner recovery cannot unwind or replace the live vault position.
    vm.prank(owner);
    vm.expectRevert();
    programmableBorrower.emergencyExitVault(0);

    vm.prank(owner);
    vm.expectRevert(NotAllowed.selector);
    programmableBorrower.setVault(replacementVault);

    // Borrower adapter still contains the stranded share balance, while user
    // request and ordinary CDO flows remain gated by the still-running epoch.
    assertGt(programmableBorrower.vaultSharesBalance(), 0);
}
```

The critical assertion is that `stopEpoch()` reverts without setting `defaulted`, despite the dedicated `onDefault()` implementation already being designed to avoid vault valuation. [7](#0-6)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L330-364)
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
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L391-405)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

```

**File:** contracts/IdleCDOEpochVariant.sol (L576-637)
```text
  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
  }

  /// @notice Prevent deposits and redeems for all classes of tranches
  function _emergencyShutdown(bool isAAWithdrawAllowed) internal override {
    // prevent deposits
    if (!paused()) {
      _pause();
    }
    // Preserve AA requests only if they were already open. This keeps a forced mid-epoch loss or
    // prior emergency from reopening them, while a normal explicit-loss stop can leave them open.
    if (!isAAWithdrawAllowed) {
      allowAAWithdrawRequest = false;
    }
    allowBBWithdrawRequest = false;
    // Persist the emergency state and let authorized forced accounting crystallize the loss.
    skipDefaultCheck = true;
  }

  /// @notice allow deposits and redeems for all classes of tranches
  /// @dev can be called by the owner only
  function restoreOperations() external override {
    _checkOnlyOwner();
    // Check if the pool was defaulted
    _checkNotAllowed(defaulted || priceAA == 0);
    skipDefaultCheck = false;
    // During an epoch ordinary deposits and withdrawal requests must remain disabled. Clearing
    // the emergency flag intentionally restores only the dedicated depositDuringEpoch path.
    if (isEpochRunning) return;
    if (paused()) {
      _unpause();
    }
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
  }

  /// @notice Prevent external unpause while an epoch, emergency shutdown, or hard default is active.
  function _beforeUnpause() internal view override {
    _checkNotAllowed(defaulted || isEpochRunning || skipDefaultCheck);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-170)
```text
  /// @notice Set the vault used to deploy idle funds.
  /// @dev Owner or manager. This does not migrate an existing position. The operator must first
  /// withdraw from the old vault and wait until epoch accounting is inactive, otherwise assets can
  /// remain stranded there and the live accounting views will stop including them after the switch.
  /// @param _vault new ERC4626 vault address
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L231-253)
```text
  function onStopEpoch(uint256 _amountRequired, bool _isRequestingAllFunds) external nonReentrant returns (bool success) {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // if we want to close the pool and the borrower still owes any amount, consider it a failure and let IdleCDO handle it as a default instead of a close. 
    if (_isRequestingAllFunds && (borrowerPrincipal != 0 || borrowerInterestDebt != 0 || borrowerInterestAccrued != 0)) {
      return false;
    }

    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L285-301)
```text
  /// @notice Abort active epoch accounting after IdleCDO defaulted the facility.
  /// @dev This keeps borrower-side epoch state aligned with IdleCDO's stopped/defaulted state
  /// without depending on a live ERC4626 valuation. A hard default is terminal for the normal
  /// epoch flow, so there is no next buffer period that needs a `bufferStartVaultAssets` baseline.
  function onDefault() external nonReentrant {
    _checkOnlyIdleCDO();
    if (!epochAccountingActive) return;

    bufferedVaultDelta = 0;
    bufferInterest = 0;
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-349)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }

  /// @notice current borrowable liquidity excluding reserved withdraw requests
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L357-372)
```text
  /// @notice Emergency escape: redeem vault shares back to this contract.
  /// @dev Owner or manager. Pass 0 to redeem all shares.
  /// @param _shares number of vault shares to redeem (0 = redeem all)
  /// @return assets amount of underlying redeemed
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
      epochWithdrawnFromVault += assets;
    }
    emit RedeemedFromVault(_shares, assets, address(this));
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L596-605)
```text
  /// @notice Emergency token rescue.
  /// @dev This is fully owner-trusted and can move the underlying as well, so it should only be
  /// used to recover stuck funds or to unwind a broken external integration.
  /// @param _token token address to transfer
  /// @param _to recipient
  /// @param _amount amount to transfer
  function rescueTokens(address _token, address _to, uint256 _amount) external onlyOwner {
    if (_to == address(0)) revert InvalidAddress();
    IERC20Detailed(_token).safeTransfer(_to, _amount);
  }
```
