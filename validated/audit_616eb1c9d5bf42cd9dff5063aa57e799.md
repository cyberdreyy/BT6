### Title
Unbounded management-fee accrual can permanently revert vault accounting - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`managementFee` is capped at 2% annually, but the cumulative fee rate is not capped against elapsed time. After more than 50 years without an accounting checkpoint, `_accrueManagementFee()` can make `unclaimedFees` exceed the strategy-token balance, causing `getContractValue()` to underflow and every deposit, withdrawal request, or epoch settlement that invokes accounting to revert. [1](#0-0) [2](#0-1) 

### Finding Description
`latestHarvestBlock` is initialized as a timestamp and is used as the last management-fee checkpoint. [3](#0-2) 

`setFeeParams()` restricts `managementFee` to `MAX_FEE / 10`; because `MAX_FEE` is 20,000 and `FULL_ALLOC` is 100,000, the maximum annual management fee is 2%. [1](#0-0) [4](#0-3) 

`_accrueManagementFee()` adds `managedNAV * managementFee * elapsed / (FULL_ALLOC * 365 days)` to `unclaimedFees` and then updates the timestamp. [2](#0-1) 

At the maximum 2% annual rate, an elapsed period greater than 50 years produces an accrued fee greater than `_managedContractValue()`. Since `_managedContractValue()` saturates at zero but still adds the calculated fee, `unclaimedFees` can exceed the CDO’s strategy-token balance. [5](#0-4) 

`getContractValue()` then subtracts `unclaimedFees` from strategy tokens plus raw underlying without a lower bound, so a fully invested vault reverts with an arithmetic underflow once `unclaimedFees` exceeds its assets. [6](#0-5) 

Deposits call `_updateAccounting()`, which first accrues the stale fee and then calls `getContractValue()`. Withdrawal requests and epoch settlement also invoke accounting, so the same stale accrual blocks minting, redemption requests, and settlement. [7](#0-6) [8](#0-7) [9](#0-8) 

Because reverting rolls back both the `unclaimedFees` addition and the `latestHarvestBlock` update, every subsequent accounting-triggering call repeats the same calculation and reverts again. [10](#0-9) [11](#0-10) 

### Impact Explanation
An unprivileged caller can permanently block accounting by triggering `_updateAccounting()` after the vault has remained inactive long enough for accrued management fees to exceed managed assets. For a 10,000-underlying fully invested vault at the maximum 2% fee, waiting 51 years accrues approximately 10,200 underlyings of fees, leaving a 200-underlying excess that makes `getContractValue()` underflow. [12](#0-11) [2](#0-1) 

This freezes user deposits, withdrawal requests, epoch settlement, and management-fee parameter updates that depend on subsequent accounting, leaving the entire live NAV inaccessible unless an external accounting repair or sufficient additional asset backing is introduced. [13](#0-12) [14](#0-13) [9](#0-8) 

### Likelihood Explanation
Likelihood is low because triggering the underflow requires a nonzero management fee near the allowed maximum and more than 50 years without a successful accounting checkpoint. The condition nevertheless uses only owner-permitted parameters and ordinary vault inactivity; it does not require a malicious owner, borrower, manager, or oracle manipulation. [1](#0-0) [2](#0-1) 

### Recommendation
Cap cumulative management-fee accrual at the currently managed NAV before updating `unclaimedFees`, or equivalently cap the effective cumulative `feePct` below 100% and waive or explicitly resolve any excess. For example, calculate `accrued = min(_calculateManagementFee(managed, elapsed), managed)`, update `latestHarvestBlock`, and handle a fully fee-consumed vault through an explicit shutdown path rather than an arithmetic panic. [12](#0-11) [2](#0-1) 

### Proof of Concept
The following Foundry test can be added to the existing `IdleCreditVault.t.sol` fixture and uses its deployment helpers:

```solidity
function testManagementFeeAccrualBlocksAccountingAfterFiftyYears() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");

    // Configure an allowed maximum 2% annual management fee.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    _setManagementFee(2_000);

    // Create a fully invested AA position.
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // At 2% annually, 51 years accrues 102% of the managed NAV.
    vm.warp(block.timestamp + 51 * 365 days);

    // Any EOA can now trigger the stale accrual and accounting underflow.
    deal(defaultUnderlying, attacker, 1);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), 1);
    vm.expectRevert(stdError.arithmeticError);
    cdoEpoch.depositAA(1);
    vm.stopPrank();

    // The same stale accrual also blocks an existing tranche holder's request.
    vm.expectRevert(stdError.arithmeticError);
    cdoEpoch.requestWithdraw(1, address(AAtranche));
}
```

The first call enters `_deposit()`, accrues more than the managed NAV into `unclaimedFees`, and then underflows in `getContractValue()`; the second call reaches the same failure through `requestWithdraw()`. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L83-85)
```text
    // Credit vaults reuse this legacy slot as the management-fee checkpoint timestamp.
    latestHarvestBlock = block.timestamp;
    _additionalInit();
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

**File:** contracts/IdleCDOCreditVault.sol (L191-211)
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
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L222-228)
```text
  function _updateAccounting() internal virtual returns (bool shutdown) {
    _accrueManagementFee();
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;
    uint256 _lastNAV = _lastNAVAA + _lastNAVBB;
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
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

**File:** contracts/IdleCDOCreditVault.sol (L550-560)
```text
  /// @notice Checkpoint accrued management fees into `unclaimedFees`.
  /// @dev Raw underlyings are excluded because unsolicited transfers are skimmed instead of managed.
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }

  /// @notice calculate annualized management fee for a balance over a duration
  function _calculateManagementFee(uint256 _nav, uint256 _duration) internal view returns (uint256) {
    // 3153600000000 == FULL_ALLOC * 365 days
    return _nav * managementFee * _duration / 3153600000000;
```

**File:** contracts/IdleCDOStorage.sol (L8-10)
```text
  uint256 public constant FULL_ALLOC = 100000;
  // max fee, relative to FULL_ALLOC
  uint256 internal constant MAX_FEE = 20000;
```

**File:** contracts/IdleCDOEpochVariant.sol (L382-436)
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
