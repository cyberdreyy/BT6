### Title
ERC4626 vault losses are floored as zero interest, letting tranche holders exit at pre-loss NAV - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
In programmable-borrower mode, `IdleCDOEpochVariant._resolveStopEpochInterest()` treats `ProgrammableBorrower.totalInterestDueNow()` as the only pool-facing epoch result, while `ProgrammableBorrower` reduces vault losses to an unsigned interest floor of zero and never reports the principal shortfall separately. [1](#0-0) [2](#0-1) 

Because `IdleCreditVault.price()` is hardcoded to `oneToken`, the CDO's strategy-token NAV still values the vault position at par after the ERC4626 position loses value. [3](#0-2) 

### Finding Description
During a running programmable-borrower epoch, undrawn assets are held in an external ERC4626 vault and `_vaultNetInterest()` compares current convertible assets plus withdrawals with the epoch-start principal baseline. [4](#0-3) 

If the vault position loses value, `_vaultNetInterest()` returns a `loss`, but `totalInterestDueNow()` floors `totalGain - loss` at zero instead of exposing the loss to the CDO. [5](#0-4) 

`stopEpoch(0, 0)` therefore sees zero interest and a zero `_lossAmount`; in minted-interest mode it needs to pull only pending withdrawals, so it can complete successfully without burning strategy tokens or invoking the loss waterfall. [6](#0-5) [7](#0-6) 

The normal `_checkDefault()` guard cannot detect this state because the strategy reports a constant price of `oneToken`, independent of the programmable borrower's actual `totalUnderlying()` or vault-share value. [3](#0-2) [8](#0-7) 

After the stop, withdrawal requests reopen, and `requestWithdraw()` prices tranche tokens from the still-unreduced tranche NAV, burns that inflated principal from the CDO, and records the same amount as borrower/vault-funded pending claims. [9](#0-8) [10](#0-9) 

At the next stop, `onStopEpoch()` withdraws the pending receipt amount from the ERC4626 vault whenever the remaining shares still nominally cover that receipt. [11](#0-10) 

The attacker can then claim the funded receipt at par through `claimWithdrawRequest()`, leaving the remaining active tranche holders with accounting NAV but no corresponding vault assets. [12](#0-11) 

### Impact Explanation
This is direct theft and protocol insolvency rather than only a stale-price display: the first withdrawal consumes real ERC4626 liquidity that should have been shared after the loss. [13](#0-12) 

For example, suppose an attacker and a victim each deposited `500` underlying and all `1,000` sits in the ERC4626 vault during the epoch. [14](#0-13) 

An underlying-market liquidation or other real ERC4626 loss reduces the programmable borrower's vault position to `500`; `totalInterestDueNow()` returns `0`, so the CDO completes the stop with the tranche NAV still at `1,000`. [2](#0-1) 

The attacker's fair claim is `250`, but the request is booked as `500`; once funded and claimed, the attacker receives the entire remaining `500`, while the victim retains a `500` accounting claim backed by zero assets. [12](#0-11) 

The extracted excess is `250`, or `P * lossFraction`, bounded only by the attacker's tranche position and the vault liquidity remaining after the loss. [15](#0-14) 

### Likelihood Explanation
The attacker only needs to be an allowed tranche holder and an unprivileged participant in the external vault's underlying market; owner, manager, guardian, borrower, and other privileged roles can remain honest and merely perform the normal epoch transitions. [16](#0-15) 

For a Morpho-backed ERC4626 sleeve, the attacker can create real market bad debt with the same flash-loan, over-borrow, price-update, and self-liquidation pattern described by the external report; that realized loss lowers `convertToAssets()` for the programmable borrower's shares. [13](#0-12) 

The attacker must hold the tranche before or during the buffer, because deposits during a running programmable epoch are disabled, but they can request the inflated withdrawal immediately after the successful stop. [17](#0-16) 

The exploit does not rely on stealing privileged permissions or forcing a privileged call; it exploits the deterministic fact that a nonzero `vaultLoss()` is never passed into `_lossAmount` or reflected in `IdleCreditVault.price()`. [18](#0-17) [19](#0-18) 

### Recommendation
Return both net interest and principal shortfall from `ProgrammableBorrower.totalInterestDueNow()` or add a separate `epochPrincipalLoss()` value consumed by `IdleCDOEpochVariant.stopEpoch()`. [2](#0-1) 

Pass that shortfall through the existing `stopEpochWithDuration(..., _lossAmount)` / `previewLossAdjustedWithdrawFunds()` path so pending receipts and active tranche holders take the documented proportional and BB-first haircuts instead of leaving NAV at par. [20](#0-19) 

Alternatively, make `IdleCreditVault.price()` reflect `ProgrammableBorrower.totalUnderlying()` divided by strategy-token exposure rather than returning a constant `oneToken` for programmable deployments. [3](#0-2) 

### Proof of Concept
The following Foundry-fork sequence uses the existing programmable-borrower test setup and a real underlying-market bad-debt transaction to reduce the ERC4626 position; privileged actors only invoke their normal epoch functions.

```solidity
// test/foundry/ProgrammableBorrowerCreditVault.t.sol
function testVaultLossExitsAtPar() public {
    uint256 attackerDeposit = 500e6;
    uint256 victimDeposit = 500e6;

    deal(USDC, attackerLp, attackerDeposit);
    deal(USDC, victimLp, victimDeposit);

    vm.startPrank(owner);
    cdoEpoch.setIsInterestMinted(true);
    vm.stopPrank();

    vm.startPrank(attackerLp);
    underlying.approve(address(idleCDO), attackerDeposit);
    idleCDO.depositAA(attackerDeposit);
    vm.stopPrank();

    vm.startPrank(victimLp);
    underlying.approve(address(idleCDO), victimDeposit);
    idleCDO.depositAA(victimDeposit);
    vm.stopPrank();

    vm.prank(manager);
    cdoEpoch.startEpoch();

    uint256 vaultAssetsBefore =
        morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower)));
    assertEq(vaultAssetsBefore, 1_000e6);

    // Unprivileged market action, on the Morpho market backing the configured vault:
    // 1. Flash-loan collateral.
    // 2. supplyCollateral(maxCollateral) to an attacker-controlled account.
    // 3. borrow(maxBorrow) against the pre-update collateral price.
    // 4. Push the lower signed oracle update.
    // 5. liquidate() the attacker's underwater account, leaving bad debt in the market.
    // This is a real reduction of morphoVault.convertToAssets(), not a stale view read.
    _createMorphoBadDebt(/* market backing morphoVault */);

    uint256 vaultAssetsAfter =
        morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower)));
    assertEq(vaultAssetsAfter, 500e6);
    assertGt(programmableBorrower.vaultLoss(), 0);
    assertEq(programmableBorrower.totalInterestDueNow(), 0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Loss was reported only as zero interest; tranche NAV is still at par.
    assertEq(cdoEpoch.getContractValue(), 1_000e6);

    uint256 attackerTrancheBal = aaTranche.balanceOf(attackerLp);
    vm.prank(attackerLp);
    uint256 requested = cdoEpoch.requestWithdraw(attackerTrancheBal, address(aaTranche));
    assertEq(requested, 500e6); // fair pro-rata claim should have been 250e6

    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 balBefore = underlying.balanceOf(attackerLp);
    vm.prank(attackerLp);
    cdoEpoch.claimWithdrawRequest();

    assertEq(underlying.balanceOf(attackerLp) - balBefore, 500e6);
    assertEq(cdoEpoch.getContractValue(), 500e6);
    assertEq(
        morphoVault.convertToAssets(morphoVault.balanceOf(address(programmableBorrower))),
        0
    );
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L373-399)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;

    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
```

**File:** contracts/IdleCDOEpochVariant.sol (L495-499)
```text
      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-668)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L319-346)
```text
  /// @notice unrealized loss from the vault position since epoch start
  function vaultLoss() external view returns (uint256) {
    (,uint256 loss) = _vaultNetInterest();
    return loss;
  }

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L349-355)
```text
  /// @notice current borrowable liquidity excluding reserved withdraw requests
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L172-176)
```text
  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-294)
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
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L432-460)
```text
  /// @notice Preview how a realized stop-epoch loss is split between active LPs and pending receipts.
  /// @dev Without pending receipts, a loss cannot exceed its active basis. When pending receipts
  /// exist, all pending receipts share their aggregate portion of the loss pro rata because the
  /// pending bucket does not retain tranche identity. The remaining active loss is later applied
  /// by the CDO through its ordinary BB-first waterfall.
  /// @param _lossAmount realized loss amount
  /// @return pendingToFund amount of pending withdrawals that should be funded by the borrower
  /// @return activeLoss amount of loss that remains assigned to active LPs
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
  }
```

**File:** contracts/IdleCDO.sol (L566-578)
```text
  function _checkDefault() virtual internal {
    uint256 currPrice = _strategyPrice();
    if (!skipDefaultCheck) {
      // calculate if % of decrease of strategyPrice is within maxDecreaseDefault
      if (lastStrategyPrice * (FULL_ALLOC - maxDecreaseDefault) / FULL_ALLOC > currPrice) revert Default();
    }
    lastStrategyPrice = currPrice;
  }

  /// @return strategy price, in underlyings
  function _strategyPrice() internal view returns (uint256) {
    return IIdleCDOStrategy(strategy).price();
  }
```
