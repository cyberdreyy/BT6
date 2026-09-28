### Title
Solvent-but-illiquid ERC4626 withdrawals can indefinitely block `stopEpoch` - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower.onStopEpoch` treats ERC4626 share valuation as proof that liquidity can be withdrawn. An ordinary vault shareholder can remove the vault’s immediately available underlying while the programmable borrower’s shares remain sufficiently valuable, causing `vault.withdraw` to revert and the hook to rethrow `StopEpochVaultLiquidityUnavailable`. [1](#0-0) 

Because `IdleCDOEpochVariant` invokes this hook before its borrower-transfer `try/catch`, the revert propagates without entering the default path and leaves the epoch running. [2](#0-1) 

### Finding Description
During a programmable-borrower stop, `onStopEpoch` compares the cash shortfall with `_currentVaultAssets()`, which is based on `convertToAssets`. This measures the economic value of the borrower’s vault position, not the amount immediately withdrawable from the ERC4626 vault. [3](#0-2) 

An attacker who is an ordinary shareholder of the configured ERC4626 vault can execute this sequence:

1. Lenders deposit into the credit vault and create pending withdrawal requests.
2. The manager starts an epoch. The programmable borrower deposits idle assets into the shared ERC4626 vault.
3. The attacker redeems enough vault shares to leave less underlying liquidity than the programmable borrower’s stop-epoch shortfall.
4. The borrower’s remaining `convertToAssets` balance can still cover the shortfall because the vault remains solvent but illiquid.
5. After `epochEndDate`, the manager calls `stopEpoch`. `onStopEpoch` attempts `vault.withdraw(shortfall)`, the ERC4626 call reverts for insufficient liquidity, and the hook rethrows `StopEpochVaultLiquidityUnavailable`. [1](#0-0) 
6. The CDO does not catch this hook revert. The entire `stopEpoch` transaction rolls back, so `isEpochRunning` remains true and no pending withdrawal funds are collected. [2](#0-1) 

The same issue can be more severe in close-pool mode: `_interest == 1` adds the CDO’s full strategy-token principal to the amount that must be pulled, so the hook can block settlement of both pending claims and pool principal. [4](#0-3) 

### Impact Explanation
This is attacker-controllable temporary freezing of funds. The frozen amount is at least the pending withdrawal amount required by `onStopEpoch`; in close-pool mode it can approach the credit vault’s full principal plus owed interest.

The attacker does not need a privileged role. They only need enough ERC4626 vault shares or withdrawal capacity to remove the liquidity needed by the programmable borrower. The freeze persists for as long as the vault remains solvent but unable to satisfy the withdrawal. User claims cannot complete because normal withdrawal funding depends on a successful `stopEpoch`. [5](#0-4) 

### Likelihood Explanation
Likelihood is medium. The issue requires a programmable-borrower deployment and a shared ERC4626 vault in which another participant controls enough shares to drain required liquidity. No malicious borrower, manager, owner, oracle failure, or token depeg is needed. The relevant distinction—economic coverage versus immediately withdrawable liquidity—is a normal ERC4626 property, and the code currently checks only the former.

### Recommendation
Do not use `convertToAssets` coverage as a no-revert precondition for `vault.withdraw`. Before withdrawing, also check `vault.maxWithdraw(address(this))`, and handle insufficient liquidity explicitly rather than rethrowing from the hook.

Preferred remediation:

- Return a distinct liquidity-pending result, or record a retryable `LiquidityPending` state, instead of reverting inside `onStopEpoch`.
- Let the CDO catch hook failures and move epoch settlement to that explicit state.
- Provide a permissioned path to force default handling after a liquidity grace period, preventing indefinite freezing.
- If preserving the current boolean API is necessary, treat `shortfall > vault.maxWithdraw(address(this))` consistently as a stop failure and ensure the CDO reaches a defined default or retry path rather than propagating an exception.

### Proof of Concept
The repository’s deterministic `MockInvariantVault` already models the required ERC4626 condition: `convertToAssets` remains solvent while `withdrawLimit` caps immediately available liquidity. [6](#0-5)  Its `withdraw` path reverts when the requested amount exceeds that limit. [7](#0-6) 

```solidity
// test/foundry/ProgrammableBorrowerLiquidityFreeze.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "../../contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "../../contracts/interfaces/IERC4626.sol";
import {IdleCDOEpochVariant} from "../../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../../contracts/strategies/idle/IdleCreditVault.sol";
import {ProgrammableBorrower, StopEpochVaultLiquidityUnavailable} from
    "../../contracts/strategies/idle/ProgrammableBorrower.sol";

contract ProgrammableBorrowerLiquidityFreezePoC is Test {
    function testVaultLiquidityDrainBlocksStopEpoch() external {
        // Existing programmable-borrower fork fixture:
        // - cdo is an initialized IdleCDOEpochVariant.
        // - strategy is its IdleCreditVault.
        // - programmableBorrower is the configured strategy borrower.
        // - isInterestMinted == true and isProgrammableBorrower == true.
        // - A lender has an outstanding pending withdrawal and the epoch has ended.
        IdleCDOEpochVariant cdo = IdleCDOEpochVariant(CDO);
        IdleCreditVault strategy = IdleCreditVault(address(STRATEGY));
        ProgrammableBorrower programmableBorrower =
            ProgrammableBorrower(PROGRAMMABLE_BORROWER);
        IERC4626 externalVault = programmableBorrower.vault();
        IERC20Detailed underlying = programmableBorrower.underlyingToken();
        address manager = strategy.manager();
        address vaultLp = EXISTING_ERC4626_LP;

        uint256 required = strategy.pendingWithdraws();
        assertGt(required, 0);

        // An ordinary ERC4626 shareholder drains just enough liquidity.
        // The programmable borrower remains solvent by convertToAssets, but
        // the vault no longer has `required` underlying available.
        uint256 liquid = underlying.balanceOf(address(externalVault));
        uint256 drain = liquid - (required - 1);

        vm.prank(vaultLp);
        externalVault.withdraw(drain, vaultLp, vaultLp);

        assertLt(underlying.balanceOf(address(externalVault)), required);
        assertGe(
            externalVault.convertToAssets(
                externalVault.balanceOf(address(programmableBorrower))
            ),
            required
        );

        // The hook revert propagates through IdleCDOEpochVariant.stopEpoch.
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        cdo.stopEpoch(0, 0);

        // The epoch remains active and the pending claim remains unfunded.
        assertTrue(cdo.isEpochRunning());
        assertEq(strategy.pendingWithdraws(), required);

        // Every retry fails while the attacker keeps the vault below the
        // required liquidity threshold.
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        cdo.stopEpoch(0, 0);
    }
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L366-376)
```text
    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
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

**File:** contracts/IdleCDOEpochVariant.sol (L555-573)
```text
  /// @notice Get funds from borrower to fullfill instant withdraw requests
  /// Manager should call this method after instantWithdrawDeadline (when epoch is running)
  /// @dev Instant withdrawals are not supported when a programmable borrower is configured.
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```

**File:** test/foundry/ProgrammableBorrowerAccountingInvariant.t.sol (L76-80)
```text
  /// @notice The withdrawable amount can be capped to simulate illiquidity.
  function maxWithdraw(address owner) external view returns (uint256) {
    uint256 maxAssets = convertToAssets(balanceOf(owner));
    return maxAssets < withdrawLimit ? maxAssets : withdrawLimit;
  }
```

**File:** test/foundry/ProgrammableBorrowerAccountingInvariant.t.sol (L113-123)
```text
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
```
