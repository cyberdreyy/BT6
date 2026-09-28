### Title
Withdrawal receipts priced at mutable mid-epoch APR let a lender lock unearned interest after an honest APR update - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external bug is "parameters that price a settlement are changed after users commit." In this codebase the analog is the vault APR: `IdleCreditVault.setApr`/`setAprs` can be called by the manager at any time, including mid-epoch, while `IdleCDOEpochVariant.requestWithdraw` prices a withdraw receipt's interest component using the APR value live at request time. `expectedEpochInterest`, by contrast, is snapshotted once in `startEpoch`. An unprivileged tranche holder who requests a withdrawal right after an honest mid-epoch APR increase gets a receipt denominated at the new, higher APR even though the epoch only earns the old one.

### Finding Description
`requestWithdraw` computes `_interest` via `_calcInterestWithdrawRequest`, which calls `_calcInterest(_managedContractValue())`, which reads `_getStrategyApr()` — the strategy's current `lastApr` [1](#0-0) . The resulting `principal + interest - fees` is minted to the user as a strategy-token receipt and added to `pendingWithdraws` [2](#0-1) [3](#0-2) .

`expectedEpochInterest`, the amount the borrower is expected to return for epoch yield, is fixed in `startEpoch` using the APR at that moment [4](#0-3) .

`IdleCreditVault.setApr` only checks `msg.sender == idleCDO || msg.sender == manager` and `maxApr`; there is no `isEpochRunning` gate, and the NatSpec explicitly documents that the manager may set the APR directly [5](#0-4) . So:

1. Epoch starts at APR = A; `expectedEpochInterest` is fixed at the A-rate.
2. Manager honestly calls `setAprs`/`setApr` raising the APR to B > A mid-epoch (a documented, permitted operation).
3. Attacker lender calls `requestWithdraw`. Their receipt is priced with interest at rate B over the full `epochDuration`.
4. At `stopEpoch`, the receipt must be funded as part of `pendingWithdraws` pulled from the borrower, while the pool only earned (and the honest borrower only owes) interest at rate A.

### Impact Explanation
The receipt embeds interest the epoch never generated. Either the borrower is forced to overpay relative to the agreed epoch interest (direct theft of the interest delta), or — if the borrower only approved the honest amount — the `getFundsFromBorrower` pull inside `_stopEpoch` reverts, the `catch` arm fires, and `_handleBorrowerDefault` marks the vault defaulted, crystallizing a spurious default and freezing/mis-pricing all tranche positions [6](#0-5) . Loss scales with `(B − A) × epochDuration × requested principal`, bounded only by `maxApr` (which can be 0 = uncapped) and the attacker's tranche balance.

### Likelihood Explanation
Requires only a KYC-passed lender holding tranche tokens and an ordinary manager APR update during a running epoch — a normal administrative action, not an error. The attacker can watch the mempool/`setAprs` transaction and call `requestWithdraw` in the same or next block, before `stopEpoch` reprices `expectedEpochInterest`. No privileged cooperation is needed. The protocol already recognized this class for the APR0 bucket (an APR change after an APR0 request makes `stopEpoch` revert), but the fixed-APR path has no equivalent snapshot or guard [7](#0-6) .

### Recommendation
Snapshot the APR (or the per-tranche withdraw interest rate) at `startEpoch` and use the snapshotted value in `_calcInterestWithdrawRequest`, mirroring the report's "save the values when the auction/epoch starts" fix. Alternatively, gate `setApr`/`setAprs` so they revert while `isEpochRunning`, or recompute withdraw interest from `expectedEpochInterest`/`lastEpochApr` rather than the live `lastApr`.

### Proof of Concept
```solidity
// test/foundry/AprMidEpochWithdraw.sol — fork-style PoC on existing harness primitives
function testMidEpochAprBumpInflatesWithdrawReceipt() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // epoch 0 stops with APR = 10%
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(10e18, 0);

    // request at 10% APR -> baseline receipt
    uint256 receiptAtOldApr = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 4, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // expectedEpochInterest is now frozen at the 10% rate
    uint256 expected = cdoEpoch.expectedEpochInterest();

    // honest manager bumps APR to 30% mid-epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(30e18, _scaleAprWithBuffer(30e18));

    // attacker requests another withdraw: receipt priced at 30%, epoch pays 10%
    uint256 receiptAtNewApr = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 4, address(AAtranche));

    assertGt(receiptAtNewApr, receiptAtOldApr);          // unearned interest locked in
    assertEq(cdoEpoch.expectedEpochInterest(), expected); // pool obligation unchanged

    // borrower funds only the honest interest; stopEpoch pulls expected + pendingWithdraws,
    // forcing borrower overpayment or triggering _handleBorrowerDefault.
}
```

Uncertainty note: I could not fully trace `_resolveStopEpochInterest`/`prepareStopEpochWithApr0` to confirm whether the borrower pull adds `pendingWithdraws` on top of `expectedEpochInterest`; the `catch` path passing `_amountToPullFromBorrower + _pendingWithdraws` into `_handleBorrowerDefault` strongly indicates receipts are funded from the borrower pull, but the exact insolvency-vs-overpayment split should be confirmed by executing the PoC.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L260-266)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;

    // set expected epoch end date
    epochEndDate = block.timestamp + _epochDuration;
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-235)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L271-294)
```text
    }
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
    }
```

**File:** test/foundry/IdleCreditVault.t.sol (L3306-3338)
```text
  function testApr0InvariantRevertsIfAprChangesAfterApr0Request() external {
    // Scenario: APR0 request is created, then APR is changed before settlement.
    // Expectation: stopEpoch reverts to enforce APR0 lifecycle invariant.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    _forceLastEpochAprToZero();

    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
  }
```
