### Title
Tranche split changes apply retroactively to unsettled epoch yield - ([File: `contracts/IdleCDO.sol`])

### Summary

`setTrancheAPRSplitRatio` updates the AA/BB yield-split parameter without first crystallizing accrued but unaccounted yield [1](#0-0) . In `IdleCDOCreditVault`, the next accounting update computes the NAV delta since `lastNAVAA`/`lastNAVBB` and splits that entire historical delta using the newly stored `trancheAPRSplitRatio` [2](#0-1) . Consequently, an honest owner parameter change made during a running epoch causes all yield accrued under the old split to be distributed under the new split at `stopEpoch` [3](#0-2) .

### Finding Description

`_updateAccounting` calculates `totalGain` as current NAV minus the last saved NAV and allocates positive gains according to the currently stored ratio [4](#0-3) . It does not track which ratio was active while portions of that gain accrued [2](#0-1) . The privileged setter overwrites the ratio before calling `_updateAccounting` or otherwise checkpointing `lastNAVAA` and `lastNAVBB` [1](#0-0) .

For example, during a running epoch with the configured AA share at 50%, the owner may change the ratio to 100%. When the manager later calls `stopEpoch`, borrower interest is made part of the vault NAV and `_updateAccounting` allocates the full epoch gain to AA [5](#0-4) . Conversely, changing the ratio to 0 allocates previously accrued gains entirely to BB [6](#0-5) .

### Impact Explanation

The invariant that epoch yield is allocated according to the ratio in force while it accrued is broken [7](#0-6) . A tranche holder can receive a materially different payout solely because the parameter changed before settlement, while the opposing tranche permanently loses its accrued share [8](#0-7) . With an accrued net gain `G`, changing the ratio from `oldRatio` to `newRatio` moves `G * (newRatio - oldRatio) / FULL_ALLOC` of value between AA and BB, bounded by `G` less rounding [6](#0-5) .

An unprivileged tranche holder benefits economically by holding the favored class through an announced or mempool-observed owner update and then exercising the normal epoch withdrawal flow after the manager settles the epoch [9](#0-8) . The loss is a direct redistribution of realized yield between tranche holders rather than a pricing discrepancy [10](#0-9) .

### Likelihood Explanation

The sequence only requires deposits to exist, an epoch to be running, the owner to change the ratio before settlement, and the manager to execute the normal `stopEpoch` path [11](#0-10) . No default, pause, malicious privileged behavior, external oracle manipulation, or invalid ratio is required [1](#0-0) .

The existing accounting hook cannot prevent this because it deliberately uses whatever ratio is stored at settlement time [2](#0-1) . Deposits and withdrawals normally settle pending accounting, but they do not protect holders during a running credit-vault epoch where ordinary withdrawals are represented through the epoch request flow [12](#0-11) .

### Recommendation

Checkpoint accounting before assigning a new ratio:

```solidity
function setTrancheAPRSplitRatio(uint256 _trancheAPRSplitRatio) external virtual {
    _checkOnlyOwner();
    _updateAccounting();
    _checkAmountTooHigh((trancheAPRSplitRatio = _trancheAPRSplitRatio) > FULL_ALLOC);
}
```

For the credit-vault variant, use its default-safe `_forceUpdateAccounting` path if the setter must remain callable during default handling [13](#0-12) . An alternative is to restrict ratio changes to a boundary immediately after accounting has been updated, but enforcement should be in the setter rather than relying on operational ordering [1](#0-0) .

### Proof of Concept

A Foundry fork test can reuse the `IdleCreditVault.t.sol` fixture and helpers:

```solidity
function testSplitRatioChangeRetroactivelyStealsEpochYield() external {
    uint256 amountAA = 90_000 * ONE_SCALE;
    uint256 amountBB = 10_000 * ONE_SCALE;
    uint256 oldRatio = FULL_ALLOC / 2;

    // Attacker is only an unprivileged AA tranche holder.
    _depositWithUser(attacker, amountAA, true);
    _depositWithUser(bbHolder, amountBB, false);
    _transferBurnedTrancheTokens(attacker, true);
    _transferBurnedTrancheTokens(bbHolder, false);

    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(owner);
    cdoEpoch.setTrancheAPRSplitRatio(oldRatio);

    _startEpochAndCheckPrices(0);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();

    // Honest owner operation: no settlement occurs inside the setter.
    vm.prank(owner);
    cdoEpoch.setTrancheAPRSplitRatio(FULL_ALLOC);

    // Honest manager settles the epoch and realizes the borrower interest.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, expectedInterest);

    // Under the vulnerable code, the entire historical epoch gain is assigned to AA.
    uint256 navGainAA = cdoEpoch.lastNAVAA() - amountAA;
    uint256 navGainBB = cdoEpoch.lastNAVBB() - amountBB;

    assertEq(navGainAA, expectedInterest);
    assertEq(navGainBB, 0);

    // Expected under the pre-change ratio:
    // navGainAA == expectedInterest * oldRatio / FULL_ALLOC;
    // navGainBB == expectedInterest * (FULL_ALLOC - oldRatio) / FULL_ALLOC;
}
```

The relevant sequence is attacker deposit, epoch start, honest owner ratio update, honest manager `stopEpoch`, then the normal withdrawal-request and claim flow [9](#0-8) . The assertion follows directly because `_updateAccounting` evaluates the full NAV delta with `trancheAPRSplitRatio == FULL_ALLOC` [6](#0-5) .

### Citations

**File:** contracts/IdleCDO.sol (L899-903)
```text
  /// @param _trancheAPRSplitRatio new apr split ratio
  function setTrancheAPRSplitRatio(uint256 _trancheAPRSplitRatio) external virtual {
    _checkOnlyOwner();
    _checkAmountTooHigh((trancheAPRSplitRatio = _trancheAPRSplitRatio) > FULL_ALLOC);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L191-200)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
```

**File:** contracts/IdleCDOCreditVault.sol (L214-220)
```text
  /// @notice this method is called on depositXX/withdrawXX and
  /// updates the accounting of the contract and effectively splits the yield/loss between the
  /// AA and BB tranches
  /// @dev this method:
  /// - update tranche prices (priceAA and priceBB)
  /// - update net asset value for both tranches (lastNAVAA and lastNAVBB)
  /// - update fee accounting (unclaimedFees)
```

**File:** contracts/IdleCDOCreditVault.sol (L222-236)
```text
  function _updateAccounting() internal virtual returns (bool shutdown) {
    _accrueManagementFee();
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;
    uint256 _lastNAV = _lastNAVAA + _lastNAVBB;
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);
```

**File:** contracts/IdleCDOCreditVault.sol (L300-328)
```text
    // In order to correctly split the interest generated between AA and BB tranche holders
    // (according to the trancheAPRSplitRatio) we need to know how much interest/loss we gained
    // since the last price update (during a depositXX/withdrawXX)
    // To do that we need to get the current value of the assets in this contract
    // and the last saved one (always during a depositXX/withdrawXX)
    // Calculate the total gain/loss
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }

    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
```

**File:** contracts/IdleCDOCreditVault.sol (L335-337)
```text
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L488-500)
```text
  function updateAccounting() external virtual {
    _checkOnlyOwnerOrGuardian();
    _forceUpdateAccounting();
  }

  /// @notice force accounting update without reverting on default path
  function _forceUpdateAccounting() internal {
    bool wasSkippingDefaultCheck = skipDefaultCheck;
    skipDefaultCheck = true;
    // Preserve an existing emergency shutdown and any wipe reported by accounting.
    if (!_updateAccounting()) {
      skipDefaultCheck = wasSkippingDefaultCheck;
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L322-324)
```text
  function stopEpoch(uint256 _newApr, uint256 _interest) public {
    _stopEpoch(_newApr, _interest, 0);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L330-362)
```text
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;

    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );

    uint256 _totBorrowed = _beforeStopEpoch(_isRequestingAllFunds);
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-437)
```text
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

```
