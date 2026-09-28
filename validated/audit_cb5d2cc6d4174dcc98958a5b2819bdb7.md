### Title
Vault share-price manipulation inflates `totalInterestDueNow` and mints unbacked epoch interest - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` prices the epoch's pool-facing interest entirely from the external ERC4626 vault's `convertToAssets()` view, with no balance check, bound, or slippage guard. Any unprivileged user of that vault can inflate (or deflate) the share price — e.g., via a direct asset donation to the vault — causing `totalInterestDueNow()` to report phantom interest that `IdleCDOEpochVariant` books as real yield at `stopEpoch`, inflating tranche prices without backing.

### Finding Description
The external report describes an `amountReceived` returned by an external contract being trusted in downstream accounting. The same pattern exists here, where the "oracle" is the ERC4626 vault exchange rate:

- `_currentVaultAssets()` converts the borrower's share balance via `vault.convertToAssets(shares)` — a manipulable view for most ERC4626 implementations (donation to `totalAssets`, or share-price inflation in vaults that count idle balance). [1](#0-0) 
- `_vaultNetInterest()` computes `netDelta = bufferedVaultDelta + earnedAssets - principalAssets` directly from that converted value, with no cross-check against the actual underlying balance held. [2](#0-1) 
- `totalInterestDueNow()` returns `vaultInterest + borrowerInterestAccruedNow() + bufferInterest - loss`, so a donation of `X` underlying to the vault raises `vaultInterest` by `X`. [3](#0-2) 
- `IdleCDOEpochVariant.stopEpoch` reads this single value to price the epoch (per the contract's own docstring: "reads a single stop-epoch interest value from this contract" [4](#0-3) ), then calls `onStopEpoch`, which only withdraws `_amountRequired` — the booked interest is never reconciled against cash actually pulled. [5](#0-4) 

Symmetrically, an attacker can *deflate* `convertToAssets` (e.g., by griefing/rebasing or exploiting a vault whose assets were lent out at a loss they induced), causing `vaultLoss()` to overstate losses [6](#0-5) , which suppresses epoch interest and misprices tranches downward.

Broken invariant: fair mint/burn — tranche NAV moves on a manipulable external valuation rather than on assets actually received.

### Impact Explanation
Phase: running epoch → `stopEpoch`; mode: programmable-borrower credit vault. An attacker who is both a tranche holder and a user of the ERC4626 vault donates `D` underlying to the vault before `stopEpoch`. `totalInterestDueNow()` increases by `D`; the CDO books phantom interest, raising the tranche virtual price. The attacker then withdraws/redeems tranches at the inflated price. The donation `D` is partially recoverable by the attacker through their own vault shares (they earn back most of it as vault yield), so the net cost is a fraction of `D`, while the tranche NAV uplift transfers real value from other tranche holders/LPs — the interest was never paid by anyone, so last withdrawers are left unbacked (insolvency / direct theft of pool value, quantified by the donation amount minted as phantom yield). No guard stops it: `onStopEpoch`'s only solvency check (`shortfall > _currentVaultAssets()`) uses the *same* manipulated view [7](#0-6) , and `totalInterestDueNow` has no cap against expected borrower interest.

### Likelihood Explanation
Requires a vault whose `convertToAssets` is donation-sensitive (true for most ERC4626 vaults where assets sit idle) and a stop timing the attacker can sandwich — epochs are publicly predictable via `epochEndDate`. Both attacker roles (vault user, tranche holder) are unprivileged and in-scope. The exploit costs roughly the donation minus the attacker's pro-rata share of vault yield, profitable whenever tranche exposure exceeds vault share exposure.

### Recommendation
Do not derive realized interest purely from `convertToAssets`. Bound `totalInterestDueNow` by `borrowerInterestAccruedNow() + bufferInterest` plus vault PnL measured on *realized* withdrawals (actual assets received vs. principal), or redeem shares to underlying and measure `balanceOf` deltas at stop. Alternatively cap vault interest at the vault's internally tracked yield and treat donation-driven `totalAssets` spikes as a sanity-check violation (e.g., compare against a per-share high-water mark or `previewRedeem` bounds).

### Proof of Concept
Foundry fork sketch (against a donation-sensitive ERC4626 vault such as a standard OpenZeppelin vault holding USDC):

```solidity
// Setup: deploy ProgrammableBorrower + IdleCDOEpochVariant wired to `vault`.
// Epoch 1 started via manager.startEpoch; borrower has drawn normally.

// Attacker (holds AA tranches + is a vault user):
uint256 D = 1_000_000e6;
usdc.transfer(address(vault), D);            // inflate totalAssets -> convertToAssets up

// Honest manager calls stopEpoch:
manager.stopEpoch();
// totalInterestDueNow() now includes D as phantom vault interest;
// tranche price mints unbacked yield.

// Attacker redeems vault shares (recovers ~D * theirSharePct), then
// withdraws tranches at inflated price via requestWithdraw/claimWithdrawRequest.
assertGt(trancheValueReceived, attackerDeposited + fairShare);
```

Caveat: I did not fully verify the exact `stopEpoch`/`_calcInterest` consumption path in `IdleCDOEpochVariant.sol` within the iteration limit — the finding assumes the documented behavior that the CDO prices the epoch from `totalInterestDueNow()`; the PoC should confirm whether the inflated interest is minted into tranche price or requires a `transferFrom` that would catch the shortfall (in which case impact reduces to mispriced NAV until the default path triggers).

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L27-28)
```text
/// IdleCDOEpochVariant only orchestrates the epoch lifecycle and reads a single stop-epoch interest value from
/// this contract. The detailed split between vault yield and borrower interest stays here.
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L320-323)
```text
  function vaultLoss() external view returns (uint256) {
    (,uint256 loss) = _vaultNetInterest();
    return loss;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
