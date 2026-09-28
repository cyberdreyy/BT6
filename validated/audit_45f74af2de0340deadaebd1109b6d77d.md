### Title
Unprivileged ERC4626 vault user can block `stopEpoch` settlement by draining vault liquidity, freezing all lender funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
In programmable-borrower mode, epoch settlement trusts an external ERC4626 vault's liquidity at the exact moment `stopEpoch` runs — analogous to CVE-2018-1000828, where the client trusted externally supplied data on an update path. `ProgrammableBorrower.onStopEpoch` checks coverage with `_currentVaultAssets()` (share price × shares) but then calls `vault.withdraw(shortfall)`, which reverts if the vault has insufficient *liquid* assets even though shares are fully covered. Any unprivileged user of that vault (e.g. a MetaMorpho depositor, or a borrower in the underlying Morpho market) can withdraw or borrow the vault's liquidity in the same block, forcing `StopEpochVaultLiquidityUnavailable` and reverting the CDO's `stopEpoch`.

### Finding Description
`IdleCDOEpochVariant.stopEpoch` calls `IProgrammableBorrower.onStopEpoch(_amountRequired, _isRequestingAllFunds)` before pulling funds. The hook logic at `contracts/strategies/idle/ProgrammableBorrower.sol:239-253`:

```solidity
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true; // covered check uses convertToAssets
  try vault.withdraw(shortfall, address(this), address(this)) returns (...) {...}
  catch { revert StopEpochVaultLiquidityUnavailable(); }
}
```

`_currentVaultAssets()` is `vault.convertToAssets(balanceOf(this))` — an accounting valuation, not `maxWithdraw`. An attacker who holds vault shares (or can borrow against the vault's market) drains the vault's idle liquidity right before the manager's `stopEpoch` transaction. Then `shortfall <= _currentVaultAssets()` (shares are solvent) so the early-`return true` default path is skipped, but `vault.withdraw` reverts for lack of liquidity and the catch block reverts the whole `stopEpoch`.

Because the revert happens inside `onStopEpoch`, the CDO never reaches its `transferFrom`-based default handling — the epoch stays `isEpochRunning` with all deposits locked (deposits and withdraw requests are paused during a running epoch). The attacker repeats the drain (or keeps Morpho utilization at ~100%) each time the manager retries.

### Impact Explanation
Temporary (potentially indefinite, at attacker's leisure) freezing of the entire pool's TVL: no lender can claim, deposit, or request withdraw while the epoch is stuck running. Attacker cost is only the cost of holding vault liquidity away (forgone yield or Morpho borrow interest), with no capital at risk. This also blocks the honest borrower's settlement and can be timed to strand the pool across rate/liquidity regime changes.

### Likelihood Explanation
Requires only an unprivileged position in the external ERC4626 vault (or its underlying market) and observing the manager's `stopEpoch` in the mempool — no privileged role needed. For a liquid public vault like MetaMorpho, flash-sized withdrawals or borrows are routinely executable in a single transaction.

### Recommendation
Use `vault.maxWithdraw(address(this))` (or `maxRedeem`) instead of `_currentVaultAssets()` for the coverage check in `onStopEpoch`: if `shortfall > maxWithdraw`, return `true` so the CDO proceeds to its transferFrom/default path rather than reverting. Alternatively, catch the withdrawal failure and return `true` (letting the subsequent `transferFrom` shortfall drive the default flow), or allow `stopEpoch` to settle the on-hand portion and mark the remainder as defaulted.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IERC4626} from "../contracts/interfaces/IERC4626.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

/// Fork test against the deployed MetaMorpho vault used in
/// test/foundry/ProgrammableBorrowerCreditVault.t.sol setup.
contract StopEpochLiquidityGriefTest is Test {
    // reuse addresses from ProgrammableBorrowerCreditVault.t.sol
    ProgrammableBorrower pb;
    IdleCDOEpochVariant cdo;
    IERC4626 morphoVault;
    IERC20Detailed underlying;
    address manager;
    address whaleVaultUser = makeAddr("whaleVaultUser");

    function testVaultLiquidityDrainBlocksStopEpoch() external {
        // 1. Normal flow: lender deposits AA, epoch starts, funds parked in vault.
        //    (setup identical to testProgrammableBorrowerStopEpochAutoRealizesInterestWithRealVault)
        //    idleCDO.depositAA(10_000e6); manager startEpoch(); warp past epochEndDate.

        // 2. Attacker (ordinary vault shareholder) sees stopEpoch in mempool and
        //    redeems enough shares / borrows enough liquidity so that
        //    vault.maxWithdraw(pb) < shortfall <= vault.convertToAssets(pbShares).
        uint256 pbShares = morphoVault.balanceOf(address(pb));
        uint256 shortfall = morphoVault.convertToAssets(pbShares); // required - onHand
        uint256 liquidityToDrain = underlying.balanceOf(address(morphoVault))
            - morphoVault.maxWithdraw(address(pb)) + shortfall; // leave maxWithdraw < shortfall
        deal(address(underlying), whaleVaultUser, liquidityToDrain); // stand-in for owning shares
        vm.startPrank(whaleVaultUser);
        underlying.approve(address(morphoVault), liquidityToDrain);
        uint256 shares = morphoVault.deposit(liquidityToDrain, whaleVaultUser);
        morphoVault.redeem(shares, whaleVaultUser, whaleVaultUser); // or borrow on Morpho market
        vm.stopPrank();
        assertLt(morphoVault.maxWithdraw(address(pb)), shortfall);

        // 3. Manager's stopEpoch reverts inside onStopEpoch even though pb is fully covered.
        vm.prank(manager);
        vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
        cdo.stopEpoch(0, 0);

        // 4. Pool is stuck: epoch still running, deposits/requests paused, default path unreachable
        //    because onStopEpoch reverted before transferFrom could fail.
        assertTrue(cdo.isEpochRunning());
        assertFalse(cdo.defaulted());
    }
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/interfaces/IProgrammableBorrower.sol (L10-14)
```text
  /// @notice Free enough liquidity so IdleCDO can pull stop-epoch funds.
  /// @param _amountRequired Total underlyings IdleCDO will transfer from the borrower.
  /// @param _isRequestingAllFunds Whether IdleCDO is closing the pool and recalling all funds.
  /// @return success False when the borrower ledger makes close-pool settlement a real default.
  function onStopEpoch(uint256 _amountRequired, bool _isRequestingAllFunds) external returns (bool success);
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-250)
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
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```
