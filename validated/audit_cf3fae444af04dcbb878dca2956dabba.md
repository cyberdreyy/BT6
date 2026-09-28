### Title

ERC4626 liquidity exhaustion can indefinitely block epoch settlement and freeze LP funds - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary

`ProgrammableBorrower.onStopEpoch` treats `convertToAssets` as proof that the requested liquidity is withdrawable, but a nominally solvent ERC4626 position can still have no currently withdrawable cash. [1](#0-0)  If `vault.withdraw` reverts, the `StopEpochVaultLiquidityUnavailable` error propagates through `IdleCDOEpochVariant.stopEpoch`, leaving `isEpochRunning` enabled and indefinitely freezing the credit vault. [2](#0-1) 

### Finding Description

In programmable mode, `IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower.onStopEpoch` before pulling borrower funds. [2](#0-1)  When the programmable borrower lacks enough on-hand underlying, `onStopEpoch` compares the shortfall against `_currentVaultAssets`, which is only `convertToAssets(shares)` and does not represent immediately withdrawable liquidity. [3](#0-2) [4](#0-3)  If the vault is asset-backed but temporarily illiquid, `vault.withdraw` reverts and `onStopEpoch` deliberately reverts with `StopEpochVaultLiquidityUnavailable`. [5](#0-4) 

Because the revert occurs before `epochAccountingActive` is cleared and before the CDO can finish stopping the epoch, the pool remains in the running state. [6](#0-5)  While that state remains active, deposits are paused and new AA/BB withdrawal requests are disabled. [7](#0-6)  An unprivileged participant in the external ERC4626 market can repeatedly remove the vault’s liquid balance—for example by borrowing or withdrawing the remaining cash—while the vault’s share valuation continues to report sufficient assets.

### Impact Explanation

All LP principal and matured withdrawal receipts dependent on the programmable borrower remain frozen for as long as the ERC4626 vault has insufficient withdrawable liquidity. The quantified exposure is the programmable borrower’s full `totalUnderlying()` plus any pending withdrawal amount that must be sourced from the vault at epoch stop. [8](#0-7)  The attack does not require control of the Idle owner, manager, guardian, borrower, fee receiver, queue, or Keyring admin.

The emergency exit does not avoid the problem because `emergencyExitVault` calls the same illiquid vault through `redeem`, which can revert under the same liquidity shortage. [9](#0-8)  Honest operators therefore cannot force epoch settlement or unwind the vault position while the attacker maintains the liquidity shortage.

### Likelihood Explanation

ERC4626 `convertToAssets` measures claim value, not instantaneous cash liquidity, so lending-style vaults can legitimately be solvent but unable to satisfy a withdrawal. [4](#0-3)  The attacker only needs the ability to keep the vault’s liquid balance below `shortfall` until after `epochEndDate`; no privileged Idle role or malformed transaction is required. [1](#0-0)  The cost is retaining the vault’s borrowed or withdrawn liquidity, but the resulting freeze can cover the entire credit-vault balance rather than only the attacker’s position.

### Recommendation

`onStopEpoch` should consult `vault.maxWithdraw(address(this))` in addition to `convertToAssets` and should handle a failed withdrawal as an explicit settlement state rather than reverting forever. A bounded retry window followed by owner-invoked liquidity-default settlement, or a non-reverting return value that lets `IdleCDOEpochVariant` enter the existing default/recovery path, would prevent an external liquidity failure from permanently pinning `isEpochRunning`. `emergencyExitVault` should similarly have a recovery path that does not depend on the same ERC4626 liquidity call succeeding. [10](#0-9) 

### Proof of Concept

The following Foundry test extends `test/foundry/ProgrammableBorrowerCreditVault.t.sol` and models an ERC4626 lending vault whose public borrower removes all liquid assets while `convertToAssets` still includes the outstanding receivable.

```solidity
contract LendingLiquidityVault is ERC20 {
  IERC20Detailed public immutable assetToken;
  uint256 public borrowedAssets;

  constructor(address asset_) ERC20("Illiquid Lending Vault", "ILV") {
    assetToken = IERC20Detailed(asset_);
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function totalAssets() public view returns (uint256) {
    return assetToken.balanceOf(address(this)) + borrowedAssets;
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    return supply == 0 ? shares : shares * totalAssets() / supply;
  }

  function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
    uint256 managedBefore = totalAssets();
    uint256 supply = totalSupply();
    shares = supply == 0 || managedBefore == 0
      ? assets
      : assets * supply / managedBefore;
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function borrowLiquidity(uint256 assets) external {
    borrowedAssets += assets;
    assetToken.transfer(msg.sender, assets);
  }

  function withdraw(
    uint256 assets,
    address receiver,
    address owner_
  ) external returns (uint256 shares) {
    require(assets <= assetToken.balanceOf(address(this)), "no-liquidity");
    shares = _toSharesRoundUp(assets);
    if (msg.sender != owner_) _spendAllowance(owner_, msg.sender, shares);
    _burn(owner_, shares);
    assetToken.transfer(receiver, assets);
  }

  function redeem(
    uint256 shares,
    address receiver,
    address owner_
  ) external returns (uint256 assets) {
    assets = convertToAssets(shares);
    require(assets <= assetToken.balanceOf(address(this)), "no-liquidity");
    if (msg.sender != owner_) _spendAllowance(owner_, msg.sender, shares);
    _burn(owner_, shares);
    assetToken.transfer(receiver, assets);
  }

  function _toSharesRoundUp(uint256 assets) internal view returns (uint256 shares) {
    uint256 supply = totalSupply();
    uint256 managed = totalAssets();
    if (supply == 0 || managed == 0) return assets;
    shares = assets * supply / managed;
    if (shares * managed < assets * supply) shares += 1;
  }
}

function test_UnprivilegedVaultLiquidityDrainFreezesEpochStop() external {
  LendingLiquidityVault lendingVault = new LendingLiquidityVault(USDC);
  _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, address(lendingVault));

  uint256 depositAmount = 100_000 * oneScale;
  uint256 mintedAA = idleCDO.depositAA(depositAmount);

  // Epoch 0 succeeds because no withdrawal liquidity is required yet.
  vm.prank(manager);
  cdoEpoch.startEpoch();
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // During the buffer, an LP queues a normal withdrawal.
  uint256 requested = cdoEpoch.requestWithdraw(mintedAA / 2, address(aaTranche));

  // Epoch 1 parks the programmable borrower's cash inside the ERC4626 vault.
  vm.prank(manager);
  cdoEpoch.startEpoch();

  // An unprivileged vault-market participant removes all liquid underlying.
  // The programmable borrower's shares remain nominally asset-backed.
  address attacker = makeAddr("vault-liquidity-attacker");
  uint256 liquid = underlying.balanceOf(address(lendingVault));
  vm.prank(attacker);
  lendingVault.borrowLiquidity(liquid);

  assertEq(underlying.balanceOf(address(lendingVault)), 0);
  assertGe(
    lendingVault.convertToAssets(lendingVault.balanceOf(address(programmableBorrower))),
    requested
  );

  // Settlement cannot complete even though the shares are nominally sufficient.
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  assertTrue(cdoEpoch.isEpochRunning());
  assertFalse(cdoEpoch.paused() == false);
  assertEq(strategy.pendingWithdraws(), requested);
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-267)
```text
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
    }

    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
    success = true;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-370)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L535-548)
```text
  /// @notice Return the total exposure in underlying terms (on-hand plus vault position).
  function totalUnderlying() external view returns (uint256) {
    return underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
  }

  /// @notice Return the current vault share balance held by this contract.
  function vaultSharesBalance() external view returns (uint256) {
    return vault.balanceOf(address(this));
  }

  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
```

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
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
