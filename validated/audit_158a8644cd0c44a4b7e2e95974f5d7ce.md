### Title
Withdrawal receipts keep stale projected APR yield after an APR reduction - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`requestWithdraw` converts tranche tokens into a fixed underlying receipt using the currently configured epoch APR, but the receipt remains payable even if the manager later lowers the vault APR before `startEpoch`. A lender can request a withdrawal while a high APR is active, wait for an APR reduction, and still claim the high-APR receipt from the borrower.

### Finding Description
A normal withdrawal request calculates `principal + projected interest - fees`, adds that fixed value to `pendingWithdraws`, burns the tranche tokens, and removes only the principal from live NAV. [1](#0-0) 

The projected interest is derived from the strategy’s current APR through `_calcInterest`, while the final claimable amount is recorded immediately rather than being repriced at epoch start. [2](#0-1) [3](#0-2) 

`IdleCreditVault.setAprsWithBuffer` allows the manager to replace the APR without checking whether withdrawal requests have already been priced under the old APR. [4](#0-3) 

At the next `startEpoch`, `expectedEpochInterest` is calculated from the reduced active NAV and the new APR, but the previously fixed `pendingWithdraws` amount remains a separate borrower liability. [5](#0-4) [6](#0-5) 

At `stopEpoch`, the CDO pulls both epoch interest and the full pending withdrawal basis from the borrower, then `collectWithdrawFunds` reserves that basis for claims. [7](#0-6) [8](#0-7) 

The requester can subsequently claim the entire stale quote because funded receipts are paid at their recorded amount. [9](#0-8) 

### Impact Explanation
An unprivileged KYC-passed lender can receive yield that was removed from the borrower’s actual next-epoch obligation after the withdrawal was requested.

For example, with 100 units each in AA and BB and a 10% quoted APR, a full BB withdrawal can lock approximately 15 units of projected interest when BB receives 75% of yield. If the APR is then reduced to zero before `startEpoch`, the receipt remains approximately 115 units instead of reverting to its principal-based value, producing approximately 15 units of excess borrower liability.

If the borrower funds the request, the excess is directly transferred to the requester; otherwise, the withdrawal can contribute to borrower default and socialize the loss across active lenders and receipt holders.

### Likelihood Explanation
The vulnerable sequence requires only a withdrawal request followed by a permitted APR update before the next epoch starts. The attacker does not control privileged calls, but can monitor pending APR changes and request a withdrawal while the stale APR quote is still available.

The withdrawal path itself is a normal buffer-phase operation, and no guard invalidates or reprices receipts when `setApr` or `setAprsWithBuffer` is called before `startEpoch`. [10](#0-9) [11](#0-10) 

### Recommendation
Bind each withdrawal receipt to the APR active when the next epoch starts rather than the APR observed at request time.

A suitable mitigation would be to prohibit APR changes while withdrawal requests are open, or store the request APR and recompute/cancel receipts when the configured APR changes before `startEpoch`. Alternatively, move APR0-style realized-interest settlement to all epoch withdrawal requests so their claims cannot retain yield from a superseded APR.

### Proof of Concept
The following Foundry test can be added to `test/foundry/IdleCreditVault.t.sol` alongside the existing credit-vault tests.

```solidity
function testRequestWithdrawRetainsStaleAprYield() external {
  uint256 amount = 100_000 * ONE_SCALE;

  // Create active AA and BB positions.
  idleCDO.depositAA(amount / 2);
  idleCDO.depositBB(amount / 2);

  // Run and stop an epoch. The next epoch is quoted at initialProvidedApr.
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(
    0,
    initialProvidedApr,
    _expectedFundsEndEpoch()
  );

  uint256 bbShares = IERC20Detailed(address(BBtranche))
    .balanceOf(address(this));
  uint256 principal = bbShares *
    cdoEpoch.tranchePrice(address(BBtranche)) /
    ONE_TRANCHE_TOKEN;

  // The receipt is fixed using the current high APR.
  uint256 quoted = cdoEpoch.requestWithdraw(
    bbShares,
    address(BBtranche)
  );
  assertGt(quoted, principal, "receipt contains projected yield");

  // Honest manager lowers the APR before the epoch starts.
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprsWithBuffer(
    0,
    cdoEpoch.epochDuration(),
    cdoEpoch.bufferPeriod()
  );

  _startEpochAndCheckPrices(1);

  // At APR zero there is no active yield, but the old receipt is still owed.
  uint256 borrowerDue = cdoEpoch.expectedEpochInterest() +
    IdleCreditVault(address(strategy)).pendingWithdraws();
  deal(defaultUnderlying, borrower, borrowerDue);

  vm.prank(borrower);
  IERC20Detailed(defaultUnderlying).approve(
    address(cdoEpoch),
    borrowerDue
  );

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  uint256 balanceBefore = IERC20Detailed(defaultUnderlying)
    .balanceOf(address(this));
  cdoEpoch.claimWithdrawRequest();

  // The requester still receives the stale high-APR quote.
  assertEq(
    IERC20Detailed(defaultUnderlying).balanceOf(address(this)) -
      balanceBefore,
    quoted,
    "stale APR receipt was not repriced"
  );
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

**File:** contracts/IdleCDOEpochVariant.sol (L357-364)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L739-750)
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

**File:** contracts/IdleCDOEpochVariant.sol (L800-808)
```text
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
```

**File:** contracts/IdleCDOEpochVariant.sol (L865-877)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-235)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-280)
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
