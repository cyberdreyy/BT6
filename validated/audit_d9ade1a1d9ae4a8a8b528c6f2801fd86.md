### Title
APR change before `requestWithdraw` execution silently routes users to lower-value instant receipts - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary

`IdleCDOEpochVariant.requestWithdraw` chooses between a fixed-APR withdrawal receipt and an instant-withdrawal receipt based on the strategy's `unscaledApr` at execution time. A user can generate or simulate a withdrawal while the current APR makes the normal path apply, but an honest manager APR update can be ordered first and push the transaction into instant mode. The user cannot specify the intended mode or a minimum receipt amount.

This matches the bug class of an order whose execution path and protection parameter are inferred from mutable market state instead of being fixed by the user.

### Finding Description

In buffer phase, `requestWithdraw` first converts tranche tokens to their current underlying principal value. If instant withdrawals are enabled, it compares `lastEpochApr` with `IdleCreditVault.unscaledApr()` and `instantWithdrawAprDelta`; when the APR has fallen enough, it calls `requestInstantWithdraw` and returns only `_underlyings`. [1](#0-0) 

Otherwise, the same call calculates projected epoch interest, subtracts withdrawal fees, and creates a normal receipt for `principal + interest - totalFees`. [2](#0-1) 

`IdleCreditVault.requestInstantWithdraw` mints only the passed principal amount as the user's strategy-token receipt and records it as an instant request. [3](#0-2) 

The APR can be changed by the CDO or manager through `setAprsWithBuffer`, which updates `unscaledApr`. [4](#0-3)  `stopEpoch` stores the previous APR in `lastEpochApr` before setting the next APR. [5](#0-4) 

Therefore, the execution path depends on a state variable that can change between the user's simulation/submission and transaction inclusion. There is no user-supplied mode, expected route, deadline, or minimum output guard.

### Impact Explanation

A lender can permanently lose the net projected epoch interest that a normal withdrawal receipt would have carried. For example, with a 10% APR and a 36.5-day epoch, a 1,000,000 USDC request has approximately 10,000 USDC of gross projected interest before fees. If an APR reduction crosses `instantWithdrawAprDelta` immediately before execution, the receipt is created for only the 1,000,000 USDC principal.

The lost amount is not merely a simulation mismatch: the instant receipt is minted for the smaller amount while the request does not enter the normal `pendingWithdraws` bucket that includes projected interest. [6](#0-5)  The difference remains as borrower/vault value rather than becoming payable to the user.

### Likelihood Explanation

The vulnerable configuration is a non-programmable, fixed-APR vault with instant withdrawals enabled. During the buffer phase, any manager APR update crossing `instantWithdrawAprDelta` flips subsequent requests to instant mode. [7](#0-6) 

A user transaction already broadcast for a normal withdrawal can be ordered after a public APR-setting transaction. No privileged malicious behavior is required; the loss comes from transaction ordering around an honest manager update. KYC, pause, and withdrawal-request flags do not prevent this because the transaction remains a valid withdrawal request.

### Recommendation

Let the caller explicitly select the withdrawal mode, for example with an `expectedInstant` or `WithdrawalMode` parameter, and revert if the current APR state would route the request differently. Alternatively, add a `minRequestedAmount`/deadline parameter and revert when the selected branch cannot produce at least the expected receipt value.

At minimum, instant withdrawals should require an explicit opt-in rather than being selected solely by mutable APR state.

### Proof of Concept

The following Foundry test can be added to `test/foundry/IdleCreditVault.t.sol`, whose setup already deploys `IdleCDOEpochVariant`, enables instant withdrawals, sets a 36.5-day epoch plus 5-day buffer, and disables Keyring restrictions. [8](#0-7) 

```solidity
function testRequestWithdrawModeFlipsAfterAprUpdate() public {
    address user = makeAddr('user');
    uint256 principal = 1_000_000 * ONE_SCALE;

    deal(defaultUnderlying, user, principal);
    vm.startPrank(user);
    underlying.approve(address(cdoEpoch), principal);
    idleCDO.depositAA(principal);
    vm.stopPrank();

    // Run and stop one epoch. Buffer phase reopens withdrawal requests.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    deal(defaultUnderlying, borrower, principal * 2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    uint256 trancheAmount = AAtranche.balanceOf(user);
    uint256 principalValue =
        trancheAmount * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE;

    // Quote while the APR state still selects the normal fixed-APR path.
    uint256 expectedNormalReceipt =
        cdoEpoch.maxWithdrawable(user, address(AAtranche));
    assertGt(expectedNormalReceipt, principalValue);

    // Honest manager lowers APR beyond the configured 1.5e18 delta:
    // lastEpochApr = 10e18, new unscaledApr = 8e18.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(
        8e18,
        cdoEpoch.epochDuration(),
        cdoEpoch.bufferPeriod()
    );

    // The user's transaction now enters instant mode and receives only principal.
    vm.prank(user);
    uint256 requested =
        cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));

    assertEq(requested, principalValue);
    assertEq(
        IdleCreditVault(address(strategy)).instantWithdrawsRequests(user),
        principalValue
    );
    assertLt(requested, expectedNormalReceipt);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L468-472)
```text
      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

```

**File:** contracts/IdleCDOEpochVariant.sol (L752-768)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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
```

**File:** test/foundry/IdleCreditVault.t.sol (L100-117)
```text
    cdoEpoch = IdleCDOEpochVariant(_cdo);
    uint256 epochDuration = 36.5 days;
    uint256 buffer = 5 days;
    // For testing let's support both tranches with AYS
    vm.startPrank(_owner);
    cdoEpoch.setBBDepositEnabled(true);
    cdoEpoch.setIsAYSActive(true);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(epochDuration, buffer); // set this to have an epoch during 1/10 of the year
    cdoEpoch.setKeyringParams(address(0), 0); // deactivate keyring
    vm.stopPrank();

    // we set the apr again manually for tests because we changed epoch params
    // scaled by the buffer period
    vm.startPrank(cv.manager());
    cv.setApr(_scaleAprWithBuffer(initialProvidedApr));
    vm.stopPrank();
  }
```
