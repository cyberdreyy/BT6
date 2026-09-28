### Title
Hidden programmable-vault losses let queued withdrawers drain new buffer deposits - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower.vaultLoss()` reports `0` whenever epoch accounting is inactive, even if the ERC4626 position lost value during the buffer period. A KYC-passed lender can queue a withdrawal, then—as an unprivileged user of the configured ERC4626 vault—create an unrealized or poorly surfaced vault loss before new suppliers enter. Because buffer deposits are minted from the stale tranche price while queued receipts remain payable at their old amount, the attacker's receipt can be funded using later suppliers' principal. The pool's accounting only carries forward `convertToAssets` deltas, so external-vault losses hidden by stale valuation or liquidity withdrawal mechanics are not exposed through `vaultLoss()` during the exact phase when new suppliers can deposit.

### Finding Description
After a successful stop, `_stopEpoch` unpauses deposits and reopens withdrawal requests in the buffer phase [1](#0-0) . During that phase, `requestWithdraw` prices a receipt from the current tranche price, burns its principal backing, and records it in `pendingWithdraws` [2](#0-1) . The strategy then burns the CDO's principal strategy tokens and mints the user a receipt for the full requested amount [3](#0-2) .

Buffer deposits still use `_deposit`, which calls `_updateAccounting` and then mints shares at the current tranche price [4](#0-3) . For `IdleCreditVault`, however, the strategy-token `price()` is always `oneToken`, so a loss held inside the programmable borrower's external ERC4626 position is not directly reflected in the strategy price [5](#0-4) . A supplier checking `vaultLoss()` also sees `0` outside active epoch accounting because `_vaultNetInterest()` immediately returns `(0, 0)` when `epochAccountingActive` is false [6](#0-5) [7](#0-6) .

At `startEpoch`, the borrower computes `bufferedVaultDelta` as the difference between current `convertToAssets` value and `bufferStartVaultAssets` [8](#0-7) . This does not protect a buffer depositor before entry: the supplier has already minted at the stale price, and an external ERC4626 loss that is not fully reflected by `convertToAssets` is also absent from `bufferedVaultDelta`. At the following stop, the CDO pulls `pendingWithdraws` from the programmable borrower and transfers them to `IdleCreditVault` for receipt claims [9](#0-8) . The borrower withdraws the requested shortfall from the ERC4626 vault when its local balance is insufficient [10](#0-9) .

### Impact Explanation
A KYC-passed attacker can receive the full pre-loss amount of a queued withdrawal while later buffer suppliers inherit the impaired external-vault position. If the attacker queues `W` and induces an unrecoverable external-vault loss of `L`, with a victim depositing `D >= W`, the attacker can extract `W`; the victim-bearing loss is approximately `min(W, L)` before fees and rounding, plus any residual impairment. This breaks fair mint/burn and solvency: the new supplier's shares were priced without the already-existing loss, while the attacker's old receipt was paid at the pre-loss amount.

### Likelihood Explanation
The attacker needs no privileged role: they can be an ordinary KYC-passed lender and an unprivileged participant in the configured ERC4626 vault. The sequence requires a queued withdrawal, a buffer-window loss, a later buffer deposit, and normal manager calls to start and stop the next epoch. External-vault illiquidity or concealed bad debt determines whether the queued receipt can be paid from incoming capital while the remaining pool is impaired; if no healthy liquidity remains, the attack instead manifests as delayed withdrawal and eventual loss socialization.

### Recommendation
- Make `vaultLoss()` and `totalInterestDueNow()` report the current delta against `bufferStartVaultAssets` while epoch accounting is inactive, instead of returning zero.
- During the buffer, expose the same externally valued loss in the CDO's pre-deposit accounting or block new deposits until the buffer delta has been checkpointed.
- Before accepting buffer deposits, revalue the programmable borrower's ERC4626 position and adjust strategy-token backing/tranche prices.
- Consider using `maxWithdraw`/redeemable liquidity in addition to `convertToAssets` when evaluating whether pending receipts are safely backed.
- Document that `vaultLoss()` is not a standalone health signal during inactive epochs if this accounting behavior remains intentional.

### Proof of Concept
The following Foundry fork test sketches the attack sequence against a configured programmable-borrower deployment. A real-fork vault should emulate the reported MetaMorpho condition: economic collateral is impaired, while share valuation or publicly surfaced `lostAssets` does not fully reveal it.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "../contracts/interfaces/IERC4626.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";

contract HiddenBufferVaultLossTest is Test {
    IERC20Detailed underlying;
    IERC4626 externalVault;
    IdleCDOEpochVariant cdo;
    ProgrammableBorrower borrower;
    IdleCreditVault strategy;

    address attacker = makeAddr("kycAttacker");
    address victim = makeAddr("kycVictim");
    address manager;

    uint256 attackerDeposit = 1_000_000e6;
    uint256 victimDeposit = 1_000_000e6;

    function setUp() public {
        vm.createSelectFork(vm.envString("MAINNET_RPC"));

        // Bind a deployed programmable-borrower pool:
        // underlying = IERC20Detailed(POOL_UNDERLYING);
        // cdo = IdleCDOEpochVariant(POOL_CDO);
        // strategy = IdleCreditVault(cdo.strategy());
        // borrower = ProgrammableBorrower(strategy.borrower());
        // externalVault = borrower.vault();
        // manager = cdo manager;

        // Both attacker and victim must pass the pool's Keyring/KYC wallet check.
        _allowWallet(attacker);
        _allowWallet(victim);
    }

    function testQueuedReceiptDrainsHiddenBufferLoss() public {
        // The pool is in buffer: deposits and normal withdrawal requests are open.
        assertFalse(cdo.isEpochRunning());
        assertFalse(cdo.paused());

        deal(address(underlying), attacker, attackerDeposit);
        vm.startPrank(attacker);
        underlying.approve(address(cdo), attackerDeposit);
        cdo.depositAA(attackerDeposit);
        uint256 queued = cdo.requestWithdraw(0, cdo.AATranche());
        vm.stopPrank();

        uint256 attackerReceipt = strategy.balanceOf(attacker);
        assertGt(queued, 0);
        assertGt(attackerReceipt, 0);

        // Attacker is an ordinary ERC4626-vault participant/liquidator and causes
        // economically unrecoverable assets while externalVault.convertToAssets()
        // remains stale or only partially reflects the loss.
        _causeExternalVaultHiddenLoss(externalVault);

        // The pool-facing health signal is still zero because accounting is inactive.
        assertFalse(borrower.epochAccountingActive());
        assertEq(borrower.vaultLoss(), 0);
        assertEq(borrower.totalInterestDueNow(), 0);

        // Victim enters at the stale buffer price.
        deal(address(underlying), victim, victimDeposit);
        uint256 victimShares;
        vm.startPrank(victim);
        underlying.approve(address(cdo), victimDeposit);
        victimShares = cdo.depositAA(victimDeposit);
        vm.stopPrank();
        assertGt(victimShares, 0);

        // Honest manager starts the next epoch. The stale convertToAssets delta is
        // zero or too small, so the hidden buffer loss is carried into the pool.
        vm.prank(manager);
        cdo.startEpoch();

        uint256 strategyBalanceBefore = strategy.balanceOf(address(cdo));
        uint256 pendingBefore = strategy.pendingWithdraws();
        assertGe(pendingBefore, queued);

        vm.warp(cdo.epochEndDate() + 1);

        // Stop pulls the attacker's receipt amount from the programmable borrower.
        vm.prank(manager);
        cdo.stopEpoch(0, 0);

        assertEq(strategy.pendingWithdraws(), 0);

        // The attacker claims funds that are backed by the incoming victim deposit.
        uint256 attackerBefore = underlying.balanceOf(attacker);
        vm.prank(attacker);
        cdo.claimWithdrawRequest(attacker);
        uint256 attackerClaimed = underlying.balanceOf(attacker) - attackerBefore;

        assertGe(attackerClaimed, queued - 2);

        // The victim cannot immediately claim and is left exposed to the impaired
        // external-vault sleeve/default recovery.
        assertGt(strategy.balanceOf(address(cdo)), 0);
        assertLt(
            strategy.balanceOf(address(cdo)),
            strategyBalanceBefore + victimDeposit
        );
    }

    function _allowWallet(address) internal {
        // Deployment-specific Keyring whitelist setup.
    }

    function _causeExternalVaultHiddenLoss(IERC4626) internal {
        // Fork-specific action: e.g. liquidate an underlying Morpho position while
        // leaving dust collateral, or otherwise impair recoverable liquidity without
        // reducing externalVault.convertToAssets() proportionally.
    }
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
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
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L473-483)
```text
      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-650)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L167-176)
```text
  /// @notice strategy token address
  function strategyToken() external view override returns (address) {
    return address(this);
  }

  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-280)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L204-214)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L319-323)
```text
  /// @notice unrealized loss from the vault position since epoch start
  function vaultLoss() external view returns (uint256) {
    (,uint256 loss) = _vaultNetInterest();
    return loss;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L336-346)
```text
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
```
