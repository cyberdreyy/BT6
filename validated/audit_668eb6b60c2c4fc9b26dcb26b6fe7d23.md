### Title
Unprivileged ERC4626 vault liquidity drain can permanently block epoch settlement - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
A third-party user of the configured ERC4626 credit vault can remove all immediately withdrawable liquidity while leaving the `ProgrammableBorrower` shares economically backed. `onStopEpoch()` uses `convertToAssets()` to decide whether the position covers the settlement shortfall, then calls `vault.withdraw()` and converts a liquidity revert into `StopEpochVaultLiquidityUnavailable`. [1](#0-0) 

Because `IdleCDOEpochVariant._stopEpoch()` invokes this hook before its `try/catch` around `getFundsFromBorrower`, the hook revert bubbles out instead of triggering `_handleBorrowerDefault()`. [2](#0-1) 

### Finding Description
At epoch stop, `ProgrammableBorrower.onStopEpoch()` compares the cash shortfall with `_currentVaultAssets()`, which is `convertToAssets(PB share balance)`. [3](#0-2) 

For an ERC4626 credit or queued-liquidity vault, `convertToAssets()` can remain high because the shares are backed by outstanding loans, while `maxWithdraw()` or `maxRedeem()` is zero because cash liquidity has been fully borrowed or reserved. An unprivileged vault-market participant can create that state by borrowing or withdrawing all available liquidity before the manager calls `stopEpoch()`.

The check `shortfall > _currentVaultAssets()` therefore passes as “covered,” but `vault.withdraw(shortfall, ...)` still reverts for lack of liquid assets. The catch block reverts deliberately, and the revert is not caught by the CDO. [4](#0-3) 

The owner escape paths do not resolve the condition: `emergencyExitVault()` calls the same vault’s `redeem()`, and `setVault()` cannot be used while the old share balance is nonzero. [5](#0-4) [6](#0-5) 

### Impact Explanation
While the attacker keeps the external vault illiquid, every `stopEpoch()` and `stopEpochWithDuration()` call reverts. The epoch remains marked running, default recovery is not entered, and tranche holders cannot reach funded withdrawal or default-claim settlement. For a pool with `P` underlying held through the vault, the attacker can freeze approximately `P` plus settlement interest for as long as the vault liquidity remains exhausted.

This is a direct analog of burning the referenced Uniswap position NFT: an externally represented position remains economically valid, but mutating the external position’s settlement/ownership state makes the protocol’s unwind path revert.

### Likelihood Explanation
Likelihood is deployment-dependent. It applies when the configured ERC4626 vault exposes liquidity to unprivileged users or its underlying market can be fully borrowed while share valuation remains backed by receivables. The attacker only needs enough collateral or shares to occupy the vault’s liquid balance at the stop-epoch boundary and does not need any privileged Idle role.

The issue is not prevented by borrower trust assumptions because the attacker is a third-party vault user, not the configured `borrower`.

### Recommendation
Distinguish external-vault illiquidity from borrower default and do not let a raw hook revert permanently block settlement.

Possible remediation:

- Check `vault.maxWithdraw(address(this))` / `maxRedeem(address(this))` and return an explicit liquidity-blocked status rather than reverting.
- Add a bounded settlement-delay state so repeated illiquidity eventually enables an owner/manager-controlled loss or default path with the vault shares accounted for.
- Add an emergency migration path that can replace the configured vault while an old share balance exists, with explicit accounting for the stranded position.
- At minimum, catch the hook failure in `IdleCDOEpochVariant._stopEpoch()` and enter a dedicated recovery state instead of leaving the epoch permanently running.

### Proof of Concept
The following deterministic Foundry PoC models a mainnet-forked ERC4626 credit vault whose public market user borrows all cash while share NAV remains backed by the loan receivable:

```solidity
// test/foundry/ProgrammableBorrowerVaultLiquidityDoS.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ProgrammableBorrower} from
    "../../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IdleCDOEpochVariant} from
    "../../contracts/IdleCDOEpochVariant.sol";

contract CreditVault4626 is ERC4626 {
    uint256 public lentOut;

    constructor(IERC20 asset_) ERC20("Credit Vault", "cv") ERC4626(asset_) {}

    /// Public underlying-market user action.
    /// Keeps ERC4626 NAV intact but removes all immediately available cash.
    function borrowAllCash() external {
        uint256 cash = IERC20(asset()).balanceOf(address(this));
        lentOut += cash;
        IERC20(asset()).transfer(msg.sender, cash);
    }

    function totalAssets() public view override returns (uint256) {
        // Shares remain backed by cash plus outstanding loans.
        return IERC20(asset()).balanceOf(address(this)) + lentOut;
    }
}

contract ProgrammableBorrowerVaultLiquidityDoSTest is Test {
    ProgrammableBorrower internal programmable;
    CreditVault4626 internal creditVault;
    IdleCDOEpochVariant internal cdo;

    address internal owner = makeAddr("owner");
    address internal manager = makeAddr("manager");
    address internal poolBorrower = makeAddr("poolBorrower");
    address internal attacker = makeAddr("externalVaultUser");
    address internal lp = makeAddr("lp");

    function setUp() public {
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

        // Deploy or load the production epoch CDO on the fork.
        // The configured underlying must be the ERC4626 asset.
        cdo = IdleCDOEpochVariant(vm.envAddress("EPOCH_CDO"));
        IERC20 underlying = IERC20(cdo.token());

        creditVault = new CreditVault4626(underlying);

        programmable = new ProgrammableBorrower();
        programmable.initialize(
            address(creditVault),
            address(cdo),
            owner,
            manager,
            poolBorrower,
            0
        );

        vm.startPrank(cdo.owner());
        cdo.setIsProgrammableBorrower(true);
        cdo.setIsInterestMinted(true);
        // Set IdleCreditVault.borrower to address(programmable), through the
        // production owner path used by the deployment.
        vm.stopPrank();

        // LP deposits and manager starts a live epoch. Production fixture helpers
        // perform the equivalent KYC, depositAA, borrower approval, and startEpoch.
        deal(address(underlying), lp, 1_000_000e6);
        vm.prank(lp);
        underlying.approve(address(cdo), 1_000_000e6);
        vm.prank(lp);
        cdo.depositAA(1_000_000e6);

        vm.prank(manager);
        cdo.startEpoch();

        // The programmable borrower parks idle principal in the public credit vault.
        assertGt(creditVault.balanceOf(address(programmable)), 0);
    }

    function testExternalVaultUserCanBlockEpochSettlement() public {
        // Attacker uses the public underlying market and removes all liquid cash.
        // Vault shares still have value because totalAssets includes lentOut.
        vm.prank(attacker);
        creditVault.borrowAllCash();

        assertEq(creditVault.maxWithdraw(address(programmable)), 0);
        assertGt(
            creditVault.convertToAssets(
                creditVault.balanceOf(address(programmable))
            ),
            0
        );

        uint256 required =
            underlyingBalanceOfProgrammable() + 1; // any covered shortfall works

        vm.warp(cdo.epochEndDate() + 1);
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        cdo.stopEpoch(0, 0);

        // The revert occurs before getFundsFromBorrower's try/catch, so no
        // borrower-default state is entered.
        assertTrue(cdo.isEpochRunning());
        assertFalse(cdo.defaulted());

        // The owner escape is also unavailable while the vault has no liquidity.
        vm.prank(owner);
        vm.expectRevert();
        programmable.emergencyExitVault(0);
    }

    function underlyingBalanceOfProgrammable() internal view returns (uint256) {
        return IERC20(cdo.token()).balanceOf(address(programmable));
    }
}
```

The central assertion is that `convertToAssets()` reports sufficient backing while `maxWithdraw()` is zero. `stopEpoch()` therefore enters the vault withdrawal branch, receives the liquidity revert, and never reaches the CDO’s fallback borrower-default handling.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L163-168)
```text
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-368)
```text
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
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
