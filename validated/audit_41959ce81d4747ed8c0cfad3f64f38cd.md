### Title
ERC4626 liquidity exhaustion can indefinitely block epoch settlement - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
An unprivileged user of the programmable borrower’s shared ERC4626 vault can exhaust the vault’s withdrawable liquidity before `stopEpoch`, causing `ProgrammableBorrower.onStopEpoch` to revert and leaving the epoch running with no protocol-side deadline or forced-default path. This can temporarily freeze all active LP principal and pending withdrawal claims while the external vault remains illiquid.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` invokes `onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)` before attempting to pull funds from the borrower. [1](#0-0) 

Inside `onStopEpoch`, a cash shortfall is considered economically covered whenever it does not exceed `_currentVaultAssets()`, and the adapter then calls `vault.withdraw`. If the ERC4626 vault has insufficient immediately withdrawable liquidity, the catch block reverts with `StopEpochVaultLiquidityUnavailable`. [2](#0-1) 

Because the CDO deliberately lets that hook revert bubble up, the entire `stopEpoch` transaction rolls back before `isEpochRunning` is cleared or `_handleBorrowerDefault` is reached. [1](#0-0) 

The default path is only reached when `onStopEpoch` returns `false` or when the subsequent `transferFrom` fails; an ERC4626 withdrawal revert bypasses both paths. [3](#0-2) 

An attacker who is merely an ERC4626-vault user can withdraw available vault liquidity through the vault’s normal public redemption path. If the programmable borrower’s shares remain economically valuable but cannot currently be withdrawn, `shortfall <= _currentVaultAssets()` remains true while `vault.withdraw` continues to revert, so every settlement attempt repeats the same state-preserving failure. [4](#0-3) 

There is no first-failure timestamp, grace period, settlement deadline, or alternate path that converts persistent ERC4626 illiquidity into a default or realized vault loss. Switching vaults is also unavailable during active accounting or while old-vault shares remain, and `emergencyExitVault` depends on the same external vault redemption succeeding. [5](#0-4) [6](#0-5) 

### Impact Explanation
All funds represented by the active tranche NAV remain locked in the running epoch while the vault position is illiquid. Deposits are already paused for a running epoch, withdrawal requests are disabled, and managers cannot complete `stopEpoch`, close the pool, or trigger borrower default through the normal path. [7](#0-6) [8](#0-7) 

The frozen amount is the active pool NAV plus any pending withdrawal basis that settlement must fund, bounded only by the pool’s total assets. The freeze can persist for as long as the ERC4626 vault cannot satisfy the withdrawal, with no idle-tranches-side timeout forcing resolution. [2](#0-1) 

### Likelihood Explanation
The attacker needs only a sufficient ERC4626 share position or access to a large vault shareholder capable of withdrawing the vault’s available cash shortly before the public epoch-end timestamp. No borrower, manager, owner, guardian, Keyring administrator, or other privileged Idle role is required. [9](#0-8) 

The condition is most relevant when the programmable borrower parks LP funds in a shared lending-style ERC4626 vault whose cash can be exhausted while its shares still have nonzero `convertToAssets` value. A successful exploit requires the recalled shortfall to be economically covered by vault shares but unavailable for withdrawal. [4](#0-3) 

### Recommendation
Track the first `StopEpochVaultLiquidityUnavailable` failure and enforce a settlement grace period. After the deadline, permit a privileged settlement path that either:

- marks the vault position as defaulted and applies the existing recovery/finalization flow; or
- realizes a vault loss capped at the unavailable position and settles the epoch through `stopEpochWithDuration`.

Do not indefinitely treat an economically covered but non-withdrawable ERC4626 position as retryable liquidity. Any defaulting path should account for the stranded vault shares explicitly so a later recovery can be distributed rather than silently writing them off. [4](#0-3) 

### Proof of Concept
A Foundry fork PoC can use the existing programmable-borrower deployment with a real shared ERC4626 vault:

```solidity
function testVaultLiquidityExhaustionFreezesStopEpoch() external {
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Unprivileged ERC4626-vault user removes all immediately available cash.
    // On a fork, use a holder whose redeemable shares cover the vault cash.
    uint256 liquid = underlying.balanceOf(address(morphoVault));
    vm.prank(vaultWhale);
    morphoVault.withdraw(liquid, vaultWhale, vaultWhale);

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
    assertEq(strategy.pendingWithdraws(), pendingBefore);

    // The same revert remains repeatable until external vault liquidity returns.
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
}
```

The decisive state is `underlying.balanceOf(vault) == 0` while `vault.convertToAssets(programmableBorrowerShares) >= shortfall`: the share-valuation check enters the withdrawal branch, the external withdrawal reverts, and the CDO’s epoch remains running rather than defaulting. [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L338-350)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-404)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-169)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-371)
```text
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
```
