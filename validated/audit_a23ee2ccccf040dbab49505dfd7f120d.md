### Title
A dust APR0 withdraw request permanently reverts `stopEpoch` if the manager ever raises the APR, freezing the entire vault - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external report (CVE-2025-30693) describes a low-complexity bug that hangs/crashes the server (availability) with limited unauthorized data modification. The closest credit-vault analog lives in `IdleCreditVault.prepareStopEpochWithApr0`, called synchronously by `IdleCDOEpochVariant._stopEpoch`. When any user has an open APR=0 withdraw request (`apr0TotalPrincipal != 0`) and `unscaledApr` is no longer 0, the function reverts with `NotAllowed` instead of settling the APR0 bucket at zero interest [1](#0-0) . Because this call sits **outside** the `try/catch` around `getFundsFromBorrower`, the revert propagates and every `stopEpoch`/`stopEpochWithDuration` call reverts [2](#0-1) . The epoch can never be stopped, borrower repayment can never be pulled, and all tranche holders' funds are frozen.

### Finding Description
- A lender requests a withdrawal while `unscaledApr == 0`. `requestWithdraw` routes to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` and pins `apr0Users[user].principalEpoch = epochNumber` [3](#0-2) [4](#0-3) .
- The manager (honest) later raises the APR for the next epoch via `setApr`/`setAprsWithBuffer`, setting `unscaledApr != 0` [5](#0-4) .
- At epoch end, `stopEpoch` → `prepareStopEpochWithApr0` hits `if (unscaledApr != 0) revert NotAllowed()` whenever `apr0TotalPrincipal != 0` [6](#0-5) . The revert is before the `try` block, so there is no fallback to `_handleBorrowerDefault` [7](#0-6) .
- The attacker cannot be forced to clear the bucket: during a running epoch `claimWithdrawRequest` reverts because `_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest[user]` while `epochEndDate != 0` [8](#0-7) , and `_settleApr0` refuses to settle while `reqEpoch >= epochNumber` [9](#0-8) . `epochNumber` only advances inside `deposit()` during a successful stop [10](#0-9) , which is exactly what is blocked.
- The only recovery is the manager pushing `unscaledApr` back to 0 via `setAprsWithBuffer`, stopping the epoch, then restoring the APR — a workaround that is not documented and silently re-prices the epoch.

### Impact Explanation
One unprivileged KYC'd lender with a dust-sized APR0 withdraw request (e.g. 1 wei of tranche) can freeze all vault operations: borrower interest/principal is never collected, `requestWithdraw`/`claimWithdrawRequest` stay gated behind epoch progression, and `startEpoch` is unreachable. This is a temporary (potentially indefinite, pending manager diagnosis) freeze of 100% of vault TVL — the availability half of the MariaDB bug class — plus an integrity violation: the APR0 bucket must either be silently zeroed (stealing accrued pro-rata interest owed under `apr0RateByEpoch`) or the APR schedule must be reverted, altering the agreed yield.

### Likelihood Explanation
The trigger requires only a normal withdraw request while APR is 0 plus a subsequent routine APR change by the honest manager — an ordinary operational sequence, not an exotic state. The attacker's cost is a minimum-deposit tranche position and one `requestWithdraw` call during a zero-APR window. No race, no privileged collusion, no oracle manipulation.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0` and `apr0TotalPrincipal != 0`. Instead, either:
- settle the APR0 bucket with zero interest for the new epoch (`apr0RateByEpoch[epochNumber] = 0; apr0TotalPrincipal = 0`), or
- compute the pro-rata share using the *request-epoch* APR snapshot stored per epoch rather than the live `unscaledApr`, so a later APR change cannot retroactively break pending receipts.

A regression test should assert that `stopEpoch` succeeds with an outstanding APR0 request after `setApr(nonzero)`.

### Proof of Concept
Foundry fork/harness PoC (against `test/foundry/IdleCreditVault.t.sol` fixtures):

```solidity
function testApr0RequestBlocksStopEpochAfterAprRaise() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // 1. APR = 0 epoch; attacker deposits and requests a withdraw
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);       // unscaledApr = 0
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // attacker leaves a dust APR0 request open (apr0TotalPrincipal != 0)
    cdoEpoch.requestWithdraw(1, address(AAtranche));

    // 2. honest manager raises APR for the coming epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(5e18, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    // 3. epoch runs; at end every stopEpoch reverts -> frozen
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, type(uint256).max);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);

    // attacker cannot clear the bucket mid-epoch either
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-508)
```text
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L551-555)
```text
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/IdleCDOEpochVariant.sol (L361-364)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-505)
```text
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
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

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
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }

      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
