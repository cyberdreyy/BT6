### Title
Instant-withdraw path skips the upfront management fee that normal withdraw requests pay - (contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` computes `_totalWithdrawFees(principal, interest)` (upfront management fee + net performance fee) only on the normal-request branch. The instant-withdraw branch — taken when `lastEpochApr > unscaledApr + instantWithdrawAprDelta` — burns tranche tokens and mints a strategy-token receipt at full principal value via `IdleCreditVault.requestInstantWithdraw`, with no fee deducted and no `pendingWithdrawFees` accrual [1](#0-0) . This mirrors the GorplesCoin pattern: a charge that should depend on the sender's own action is gated by an unrelated second condition (here, the APR-drop predicate), so the fee is silently skipped whenever the instant path is taken.

### Finding Description
Normal requests record `pendingWithdrawFees += totalFees` and forward `principal + interest - totalFees` to `IdleCreditVault.requestWithdraw` [2](#0-1) . The code comment justifies the upfront management fee because "the receipt is fixed now and leaves live NAV… for the time it waits outside live NAV: remaining buffer plus the next epoch" [3](#0-2) . Instant receipts created by `requestInstantWithdraw` leave live NAV identically (`_burn(msg.sender)` of strategy tokens, claimable only after the epoch deadline or full repayment per `allowInstantWithdraw`) [4](#0-3) , yet `requestWithdraw` pays them `_underlyings` at full tranche price with zero fee [5](#0-4) . At `stopEpoch`, instant claims are funded by `collectInstantWithdrawFunds`/`getInstantWithdrawFunds` at par while `pendingWithdrawFees` (which reduces `_availableForFees` and is paid to `feeReceiver`/owner) reflects only normal-request fees [6](#0-5) . No guard (`_checkNotAllowed`, `isWalletAllowed`, skim, or epoch gating) compensates: `isWalletAllowed(msg.sender)` is still checked, so any KYC'd lender can use the path when it opens.

### Impact Explanation
When an APR drop opens the instant-withdraw window, a lender holding tranche tokens exits at full principal while an identical normal request in the same epoch is haircut by `_calculateManagementFee(principal, _withdrawRequestManagementFeeDuration())` — one epoch plus remaining buffer at the `managementFee` rate. The skipped amount is never added to `pendingWithdrawFees`, so it is neither accrued to `unclaimedFees` nor paid to `feeReceiver`/owner at `stopEpoch` [7](#0-6) . Loss = `principal * managementFee * (epochDuration + remainingBuffer) / (365 days * FULL_ALLOC)` per request — theft of protocol yield (fees) rather than user principal, unbounded across repeated requests in the window.

### Likelihood Explanation
The window is externally triggerable: `unscaledApr` is lowered by honest `manager`/`setAprs` calls each epoch, so any epoch where the new APR drops by more than `instantWithdrawAprDelta` enables the path for every KYC'd holder. In non-programmable, non-disabled deployments (`_isInstantWithdrawEnabled`) this recurs naturally whenever the borrower renegotiates rate downward [8](#0-7) . Attacker needs only to be a lender calling `requestWithdraw(0, tranche)` during the window.

### Recommendation
Apply `_totalWithdrawFees` (at minimum the management-fee component for the receipt's out-of-NAV duration) on the instant branch before calling `requestInstantWithdraw`, and add it to `pendingWithdrawFees` — or document/explicitly zero the fee only if the design intends instant receipts to be fee-free, in which case the asymmetry with identical normal receipts should be removed.

### Proof of Concept
Foundry fork sketch (pattern follows `test/foundry/IdleCreditVault.t.sol` `testRequestWithdrawChargesManagementFeeUpfront` [9](#0-8) ):

```solidity
// setup: deploy IdleCDOEpochVariant + IdleCreditVault, managementFee = 1%, non-zero buffer
// attacker = KYC'd lender with AA tranche tokens
idleCDO.depositAA(amount);                          // attacker deposit
_transferBurnedTrancheTokens(attacker, true);
_startEpochAndCheckPrices(0);
// honest manager lowers APR below lastEpochApr - instantWithdrawAprDelta
vm.prank(manager);
IdleCreditVault(address(strategy)).setAprs(lowerApr, scaledApr);

uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);
uint256 principal = trancheBal * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE_TOKEN;
uint256 expectedMgmtFee = _calcManagementFee(principal, mgmtFee, _withdrawRequestManagementFeeDuration());

uint256 feesBefore = cdoEpoch.pendingWithdrawFees();
vm.prank(attacker);
uint256 requested = cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

// BUG: instant path taken -> full principal, no fee booked
assertEq(requested, principal);                        // should be principal - mgmtFee
assertEq(cdoEpoch.pendingWithdrawFees(), feesBefore);  // no fee accrued to feeReceiver/owner
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L416-459)
```text
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

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

**File:** contracts/IdleCDOEpochVariant.sol (L761-790)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L839-841)
```text
  function _isInstantWithdrawEnabled() internal view virtual returns (bool) {
    return !disableInstantWithdraw && !isProgrammableBorrower;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L1255-1291)
```text
  function testRequestWithdrawChargesManagementFeeUpfront() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 mgmtFeeRate = 1_000; // 1%

    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    _setManagementFee(mgmtFeeRate);

    uint256 trancheAmount = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    uint256 principal = trancheAmount * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE_TOKEN;
    (uint256 netInterest, ) = _calcInterestForTranche(address(AAtranche), trancheAmount);
    uint256 claimAmountPreFee = principal + netInterest;
    uint256 expectedMgmtFee = _calcManagementFee(principal, mgmtFeeRate, _withdrawRequestManagementFeeDuration());
    uint256 expectedClaimAmount = claimAmountPreFee - expectedMgmtFee;

    uint256 requested = cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));

    assertEq(requested, expectedClaimAmount, "withdraw receipt should be haircut by management fee");
    assertEq(cdoEpoch.pendingWithdrawFees(), expectedMgmtFee, "pending withdraw fees should include management fee");
    assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), expectedClaimAmount, "pending withdraws should be net of management fee");

    vm.prank(manager);
    cdoEpoch.startEpoch();

    uint256 feeReceiverBalPre = underlying.balanceOf(TL_MULTISIG);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + IdleCreditVault(address(strategy)).pendingWithdraws());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertEq(underlying.balanceOf(TL_MULTISIG) - feeReceiverBalPre, expectedMgmtFee, "fee receiver got wrong upfront management fee");

    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balPre, expectedClaimAmount, "claim amount should stay net of management fee");
  }
```
