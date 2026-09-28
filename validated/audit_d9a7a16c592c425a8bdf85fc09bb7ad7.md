### Title
Performance fee changes are applied retroactively to uncheckpointed epoch yield - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary
`setFeeParams` checkpoints only the time-based management fee before replacing `fee`, but it does not checkpoint accumulated NAV gains. The next `_updateAccounting` therefore applies the newly configured performance-fee rate to all positive NAV growth since the previous accounting checkpoint. [1](#0-0) [2](#0-1) 

### Finding Description
`setFeeParams` calls `_accrueManagementFee` and then writes `fee`, `feeSplit`, and `managementFee`. [1](#0-0) 

That checkpoint protects only `managementFee`: `_accrueManagementFee` calculates elapsed management fees using the old `managementFee` value and advances `latestHarvestBlock`. [3](#0-2) 

Performance fees remain uncheckpointed. `_updateAccounting` later computes `nav - lastNAV` and multiplies that entire historical gain by the currently stored `fee`; `_virtualPriceAux` separately removes the same current-rate fee from positive tranche gains. [4](#0-3) [5](#0-4) 

In an epoch vault, `stopEpoch` calls `_updateAccounting` after receiving epoch interest. Consequently, a `fee` increase made during a running epoch is applied to interest generated before the fee change. [6](#0-5) 

A concrete sequence is:

1. Depositors fund the vault and the honest manager calls `startEpoch`.
2. The epoch accrues its fixed-APR interest while `fee = 10%`.
3. During the running epoch, the honest owner calls `setFeeParams(..., 20%, ..., ...)`.
4. At epoch maturity, the manager calls `stopEpoch`.
5. `_updateAccounting` charges `20%` on the full epoch gain rather than charging `10%` through the fee-change timestamp and `20%` thereafter.

The analogous decrease is also possible: accrued yield that economically belonged under a higher historical rate is charged only the lower replacement rate.

### Impact Explanation
The fair-mint and yield-accounting invariant is broken because a rate introduced at time `t1` is applied to NAV growth accumulated between `t0` and `t2`. A fee increase transfers part of already-earned tranche yield to `feeReceiver`/`owner`; the epoch’s additional fee is approximately `(newFee - oldFee) * epochGain / FULL_ALLOC`. A fee decrease correspondingly deprives the configured fee recipients of fees accrued under the prior rate. [7](#0-6) [2](#0-1) 

### Likelihood Explanation
An unprivileged lender does not need to control the owner: the lender only needs an open position while the owner performs an ordinary `setFeeParams` call before a deposit, withdrawal, forced accounting call, or `stopEpoch`. In epoch mode, `stopEpoch` is the deterministic checkpoint that realizes the entire epoch’s NAV gain, making retroactive application reliably observable. [8](#0-7) [9](#0-8) 

### Recommendation
Checkpoint the performance fee before assigning a new `fee` value. `setFeeParams` should force accounting before mutation, while continuing to preserve default handling semantics:

```solidity
function setFeeParams(
    address _feeReceiver,
    uint256 _fee,
    uint256 _feeSplit,
    uint256 _managementFee
) external {
    _checkOnlyOwner();
    _checkAmountTooHigh(
        _fee > MAX_FEE ||
        _feeSplit > FULL_ALLOC ||
        _managementFee > MAX_FEE / 10
    );

    _skimDonatedAssets();
    _forceUpdateAccounting(); // checkpoints management and performance fees

    _checkIs0((feeReceiver = _feeReceiver) == address(0));
    fee = _fee;
    feeSplit = _feeSplit;
    managementFee = _managementFee;
}
```

If changing fees while a loss would trigger `Default` must remain possible, add a dedicated checkpoint that records pending performance-fee basis without reverting through the default path, rather than silently repricing all historical gains.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "./IdleCreditVault.t.sol";

contract RetroactivePerformanceFeePoC is IdleCreditVaultTest {
    function testPerformanceFeeChangeAppliesRetroactively() external {
        uint256 amount = 10_000 * ONE_SCALE;
        uint256 oldFee = 10_000; // 10%
        uint256 newFee = 20_000; // 20%

        // Configure the original fee and a nonzero receiver.
        _setFeeParams(TL_MULTISIG, oldFee, FULL_ALLOC, 0);

        // Create an active LP position.
        idleCDO.depositAA(amount);

        // Start a fixed-APR epoch.
        vm.startPrank(manager);
        cdoEpoch.setEpochParams(365 days, 0);
        IdleCreditVault(address(strategy)).setApr(initialProvidedApr);
        cdoEpoch.startEpoch();
        vm.stopPrank();

        uint256 grossInterest = cdoEpoch.expectedEpochInterest();

        // The whole epoch accrues under oldFee, but the owner changes fee
        // before the next NAV/accounting checkpoint.
        vm.prank(owner);
        cdoEpoch.setFeeParams(TL_MULTISIG, newFee, FULL_ALLOC, 0);

        address recipient = TL_MULTISIG;
        uint256 recipientBefore = underlying.balanceOf(recipient);

        // Borrower funds the fixed epoch interest.
        deal(defaultUnderlying, borrower, grossInterest);
        vm.warp(cdoEpoch.epochEndDate());

        vm.prank(manager);
        cdoEpoch.stopEpoch(initialProvidedApr, 0);

        uint256 actualFee = underlying.balanceOf(recipient) - recipientBefore;
        uint256 oldRateFee = grossInterest * oldFee / FULL_ALLOC;
        uint256 newRateFee = grossInterest * newFee / FULL_ALLOC;

        // Fails under correct semantics: the entire historical gain is charged
        // at newFee instead of splitting the elapsed periods by their rates.
        assertEq(actualFee, oldRateFee);

        // Demonstrates the misplaced amount.
        assertGt(actualFee, oldRateFee);
        assertEq(actualFee, newRateFee);
    }
}
```

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L222-233)
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
```

**File:** contracts/IdleCDOCreditVault.sol (L306-314)
```text
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L461-469)
```text
  function setFeeParams(address _feeReceiver, uint256 _fee, uint256 _feeSplit, uint256 _managementFee) external {
    _checkOnlyOwner();
    _checkAmountTooHigh(_fee > MAX_FEE || _feeSplit > FULL_ALLOC || _managementFee > MAX_FEE / 10);
    _checkIs0((feeReceiver = _feeReceiver) == address(0));

    _accrueManagementFee();
    fee = _fee;
    feeSplit = _feeSplit;
    managementFee = _managementFee;
```

**File:** contracts/IdleCDOCreditVault.sol (L552-560)
```text
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }

  /// @notice calculate annualized management fee for a balance over a duration
  function _calculateManagementFee(uint256 _nav, uint256 _duration) internal view returns (uint256) {
    // 3153600000000 == FULL_ALLOC * 365 days
    return _nav * managementFee * _duration / 3153600000000;
```

**File:** contracts/IdleCDOCreditVault.sol (L585-597)
```text
  /// @notice transfer fee to feeReceiver and owner according to feeSplit
  /// @param _amount total fee amount to split and transfer (in underlyings)
  function _transferFeeUnderlyings(uint256 _amount) internal {
    uint256 feeReceiverAmount = _feeReceiverAmount(_amount);
    _transferUnderlyings(feeReceiver, feeReceiverAmount);
    _transferUnderlyings(owner(), _amount - feeReceiverAmount);
  }

  /// @notice calculates the amount to transfer to feeReceiver based on the feeSplit
  /// @dev `setFeeParams` guarantees a nonzero receiver; a zero split naturally returns zero.
  /// @param _amount total fee amount to split
  function _feeReceiverAmount(uint256 _amount) internal view returns (uint256) {
    return _amount * feeSplit / FULL_ALLOC;
```

**File:** contracts/IdleCDOEpochVariant.sol (L321-336)
```text
  /// @dev Only owner or manager can call this function. Borrower MUST approve this contract
  function stopEpoch(uint256 _newApr, uint256 _interest) public {
    _stopEpoch(_newApr, _interest, 0);
  }

  /// @notice Internal stop-epoch implementation with optional proportional pending-receipt loss.
  /// @param _newApr New apr to set for the next epoch
  /// @param _interest Interest gained in the epoch
  /// @param _lossAmount Loss amount to split between active LPs and pending receipts
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L382-456)
```text
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
```
