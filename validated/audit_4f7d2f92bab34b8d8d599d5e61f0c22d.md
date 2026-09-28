### Title
Stale-APR withdrawal requests can front-run rate cuts and lock excess interest - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`requestWithdraw` fixes a withdrawal receipt using the strategy’s current APR, but the manager can change that APR during the withdrawal window without a nonce, deadline, or rate-transition guard. A whitelisted tranche holder who observes a pending APR reduction can request withdrawal before the update and lock the old higher rate for the next epoch, while later requesters are routed to instant withdrawal or priced at the reduced rate. [1](#0-0) [2](#0-1) 

### Finding Description
During the buffer phase, `allowAAWithdrawRequest` and `allowBBWithdrawRequest` are open, and `requestWithdraw` computes a fixed underlying receipt from the current tranche price and projected next-epoch interest. [3](#0-2) [4](#0-3) 

The projected interest is derived from `_getStrategyApr()`, which reads `IdleCreditVault.lastApr`. [5](#0-4) [6](#0-5) 

`IdleCreditVault.setAprs` and `setApr` can be called by the honest manager even between epochs and do not invalidate withdrawal quotes already formed against the old rate. [7](#0-6) 

Once created, the inflated receipt is minted to the user and added to `pendingWithdraws`, so the next successful `stopEpoch` must source that stale-rate amount from the borrower. [8](#0-7) [9](#0-8) 

This mirrors the reported bug class: the outcome is fully computable from public state, and the attacker wins by choosing transaction timing relative to an honest privileged state transition rather than by manipulating privileged code. [10](#0-9) 

### Impact Explanation
For a 365-day epoch and a rate cut from 10% to 0%, a 1,000-token withdrawal requested before `setAprs(0, 0)` creates a receipt for approximately 1,100 tokens, while the intended 0% next-epoch quote is only the 1,000-token principal. [11](#0-10) [12](#0-11) 

The borrower is charged 100 excess tokens, or the pool can enter borrower-default handling if the borrower only approves or holds the corrected amount. [13](#0-12) 

### Likelihood Explanation
The attack requires a KYC-passed tranche holder to observe or predict a pending manager APR reduction during the buffer window. [14](#0-13) 

Manager rate changes are valid between epochs because `setApr` has no epoch-state restriction, and ordinary mempool ordering can place the attacker’s `requestWithdraw` before the rate update. [2](#0-1) 

The instant-withdrawal protection does not stop the stale quote because it checks `unscaledApr` only at request execution time. [1](#0-0) 

### Recommendation
Bind withdrawal-request pricing to a committed epoch-rate version or process APR changes and withdrawal requests through the same privileged epoch transition. At minimum, record the APR quote epoch and reject requests if `lastApr`, `unscaledApr`, or an APR nonce changed during the current buffer. Alternatively, require all next-epoch APR changes to occur atomically inside `stopEpoch`, where they take effect before requests reopen, and remove direct manager APR updates while withdrawal requests are open. [15](#0-14) [2](#0-1) 

### Proof of Concept
The following Foundry test uses the existing `IdleCreditVault.t.sol` fixture and demonstrates the stale-rate receipt:

```solidity
function testRequestWithdrawFrontRunsAprCut() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

    uint256 principal = 1_000 * ONE_SCALE;
    idleCDO.depositAA(principal);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    uint256 snapshot = vm.snapshot();

    // Baseline: if the APR is reduced first, the request becomes an instant
    // withdrawal and only principal is escrowed.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    uint256 afterCutQuote = cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(afterCutQuote, principal);
    vm.revertTo(snapshot);

    // Attack path: request while the stale high APR is still active.
    uint256 staleQuote = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // The honest manager then lowers the APR for the next epoch to zero.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    assertEq(pending, staleQuote);
    assertGt(staleQuote, principal);

    deal(defaultUnderlying, borrower, pending);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 before = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - before;

    assertEq(claimed, staleQuote);
    assertEq(claimed - principal, principal / 10); // stale 10% APR for 365 days
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L406-410)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L468-482)
```text
      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
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

**File:** contracts/IdleCDOEpochVariant.sol (L798-809)
```text
  /// @notice Calculate the interest of an epoch for the given amount
  /// @param _amount Amount of underlyings
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L856-878)
```text
  function _calcInterestWithdrawRequest(uint256 _amount, address _tranche) internal view returns (uint256 _interest, int256 _diff) {
    uint256 _duration = epochDuration;
    if (_duration == 0) {
      return (_interest, _diff);
    }

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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L178-181)
```text
  /// @notice current fixed apr for the epoch
  function getApr() external view returns (uint256) {
    return lastApr;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L202-235)
```text
  /// @notice set both the scaled and unscaled apr
  /// @dev only cdo and manager can set the apr.
  /// @param _unscaledApr unscaled apr
  /// @param _apr scaled apr
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-293)
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
```
