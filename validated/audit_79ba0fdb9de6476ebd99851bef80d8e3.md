### Title
Stale programmable-borrower interest quote mints unbacked epoch yield after ERC4626 withdrawal - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
`IdleCDOEpochVariant.stopEpoch` resolves the programmable-borrower interest amount before `ProgrammableBorrower.onStopEpoch` withdraws ERC4626 liquidity. If that withdrawal reduces the borrower's vault position through a withdrawal fee, share-price loss, or unfavorable conversion, the CDO still mints strategy tokens using the pre-withdrawal interest value, leaving epoch interest partially unbacked. [1](#0-0) [2](#0-1) 

### Finding Description
`_stopEpoch` calls `_resolveStopEpochInterest(_interest)` before invoking `IProgrammableBorrower.onStopEpoch`, so the programmable-borrower quote is sampled before the external vault is touched. [3](#0-2) [4](#0-3) 

In `ProgrammableBorrower.onStopEpoch`, any cash shortfall is withdrawn from the ERC4626 vault and `epochWithdrawnFromVault` is incremented by the requested asset amount. [5](#0-4) 

`totalInterestDueNow` derives pool-facing interest from the live vault position through `_vaultNetInterest`, so an economically lossy withdrawal changes the correct interest result after the withdrawal. [6](#0-5) 

The CDO nevertheless stores and mints the stale `_grossInterest` after the hook returns. [7](#0-6) [8](#0-7) 

For example, assume the programmable borrower has `1,000` vault assets, the vault position appreciates to `1,010`, and a lender has a `500` pending withdrawal. If the ERC4626 withdrawal burns shares worth `505` to deliver `500`, the true post-withdrawal pool gain is `5`, while the pre-withdrawal `totalInterestDueNow` is `10`. `stopEpoch` mints `10` strategy tokens even though only `5` of net vault gain remains, creating a `5`-token backing deficit. [9](#0-8) [8](#0-7) 

### Impact Explanation
The minted strategy tokens increase CDO NAV without a matching underlying or borrower-debt increase, so tranche prices and fee-share mints are overstated by the withdrawal-side loss. [10](#0-9) [11](#0-10) 

The loss equals the ERC4626 withdrawal haircut capped by the quoted epoch interest. Because withdrawal requesters are paid from the strategy reserve while active tranche holders rely on the remaining programmable-borrower position, the deficit is socialized across active tranche holders and can make late claims unbacked. [12](#0-11) [13](#0-12) 

### Likelihood Explanation
A KYC-passing lender can create the required pending withdrawal during the buffer phase, and an honest manager's later `stopEpoch` call triggers the vault withdrawal. The issue requires the pending withdrawal to exceed the programmable borrower's idle cash and requires the configured ERC4626 vault to impose a withdrawal fee or otherwise produce an unfavorable share-to-asset conversion. [14](#0-13) [5](#0-4) 

The existing `maxApr` check only caps an explicitly resolved interest override and does not compare the pre-withdrawal quote with the post-withdrawal result. [15](#0-14) 

### Recommendation
Recalculate or return `totalInterestDueNow` after `onStopEpoch` has recalled the required vault liquidity, and use that post-liquidity value for APR0 settlement, `_grossInterest`, fee accounting, and `mintStrategyTokens`. If APR0 settlement must know the withdrawal amount first, split the programmable-borrower hook into a liquidity stage and a final accounting stage, or make `onStopEpoch` return the post-withdrawal interest value. The implementation should compare the requested withdrawal amount with the actual decrease in `convertToAssets(vault.balanceOf(this))`, rather than assuming both differ only by `shortfall`. [5](#0-4) [9](#0-8) 

### Proof of Concept
The following Foundry test shape is intended for the existing programmable-borrower fork harness. It uses an ERC4626 vault whose withdrawal burns more share value than the assets delivered, demonstrates the stale quote, and asserts the resulting backing deficit.

```solidity
function testStopEpochUsesPreWithdrawalProgrammableInterest() public {
    uint256 principal = 1_000e6;
    uint256 pending = 500e6;
    uint256 vaultGain = 10e6;
    uint256 withdrawalLoss = 5e6;

    // KYC-approved lender deposits before the epoch.
    deal(address(underlying), lender, principal);
    vm.startPrank(lender);
    underlying.approve(address(cdo), principal);
    uint256 trancheAmount = cdo.depositAA(principal);
    cdo.requestWithdraw(pending, address(AAtranche));
    vm.stopPrank();

    // Honest manager starts the epoch. Idle funds are deposited into the vault.
    vm.prank(manager);
    cdo.startEpoch();

    // Real vault yield before stop.
    deal(address(underlying), address(vault), vaultGain);

    // Vault delivers `pending` cash but burns pending + withdrawalLoss
    // worth of ProgrammableBorrower share value.
    feeVault.setWithdrawalLoss(withdrawalLoss);

    uint256 quotedBefore = programmableBorrower.totalInterestDueNow();
    assertEq(quotedBefore, vaultGain);

    uint256 backingBefore =
        underlying.balanceOf(address(strategy)) +
        vault.convertToAssets(vault.balanceOf(address(programmableBorrower)));

    vm.prank(manager);
    cdo.stopEpoch(0, 0);

    uint256 backingAfter =
        underlying.balanceOf(address(strategy)) +
        vault.convertToAssets(vault.balanceOf(address(programmableBorrower)));

    // CDO minted the stale 10-unit quote while only 5 units of net gain survived.
    assertEq(strategy.balanceOf(address(cdo)), principal + quotedBefore);
    assertEq(backingAfter, backingBefore + vaultGain - withdrawalLoss);
    assertEq(
        strategy.balanceOf(address(cdo)) - backingAfter,
        withdrawalLoss
    );
}
```

The decisive sequence is `requestWithdraw` during the buffer, `startEpoch`, vault appreciation, and then the manager's `stopEpoch`. The assertion fails only if `stopEpoch` reprices programmable-borrower interest after the ERC4626 withdrawal. [1](#0-0) [16](#0-15)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L353-364)
```text
    uint256 _totBorrowed = _beforeStopEpoch(_isRequestingAllFunds);
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-380)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-415)
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
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
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

**File:** contracts/IdleCDOEpochVariant.sol (L439-459)
```text
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
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-250)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-346)
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
```

**File:** contracts/IdleCDOCreditVault.sol (L123-137)
```text
  /// @notice calculates the current net TVL (in `token` terms)
  /// @dev `unclaimedFees` are not counted.
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }

  /// @notice Calculates the current managed net TVL.
  /// @dev Raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions.
  /// @return Strategy-token-backed TVL net of accrued fees.
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```
