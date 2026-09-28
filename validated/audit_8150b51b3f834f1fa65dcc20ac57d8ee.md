### Title
APR0 withdraw bucket bricks `stopEpoch` after a routine APR change — epoch rollover DoS freezes all LP funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
An unprivileged lender who files a withdraw request while `unscaledApr == 0` permanently poisons `stopEpoch`: `prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`. Because the manager sets the next epoch's APR via `setAprs`/`setApr` independently of `stopEpoch`, an honest APR increase during the buffer turns the user's pending receipt into an "unsupported key" at rollover: every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts before any state changes, halting the epoch state machine until APR is manually reset to 0. [1](#0-0) [2](#0-1) 

### Finding Description
`requestWithdraw` in `IdleCreditVault` routes the receipt into the APR0 bucket (`apr0TotalPrincipal`, `apr0Users[user].principal`) whenever `unscaledApr == 0`. [3](#0-2)  At epoch rollover, `_stopEpoch` unconditionally calls `_strategy.prepareStopEpochWithApr0(_interest)` before pulling borrower funds or updating `epochNumber`. [4](#0-3)  If the manager raised the APR in the meantime — a normal administrative action via `setAprs`/`setApr`, callable directly at any time — the guard `if (unscaledApr != 0) revert NotAllowed()` fires, so the whole `stopEpoch` transaction reverts. [5](#0-4)  There is no user-side escape: claiming requires `epochNumber > lastWithdrawRequest`, which only advances inside a successful `stopEpoch`, and nothing besides `prepareStopEpochWithApr0`/`_clearWithdrawClaimForEpoch` decrements `apr0TotalPrincipal`. [6](#0-5)  This mirrors CVE-2018-5745: a trust-anchor rollover crashes when a newly configured algorithm is incompatible with state carried over from the previous key set — here, the new non-zero APR is incompatible with the carried-over APR0 receipt bucket, and the revert plays the role of the assertion failure.

### Impact Explanation
While `unscaledApr != 0`, every `stopEpoch` and `stopEpochWithDuration` reverts: `epochNumber` never increments, pending withdraw requests can never be funded or claimed, `collectWithdrawFunds` never runs, and the pool cannot even be closed (`_interest == 1` close-pool mode hits the same revert). All LP principal sits with the borrower and all withdraw receipts are frozen for the duration of the lock — temporary freezing of the entire TVL plus all pending receipts. The freeze ends only when the manager restores `unscaledApr = 0` to settle the bucket, i.e. the vault is forced to run a zero-interest epoch; if a non-zero APR is required (e.g. to satisfy instant-withdraw apr-delta conditions or interest owed), the freeze can persist indefinitely.

### Likelihood Explanation
Medium. Trigger requires only a KYC-passed lender calling `requestWithdraw` during a zero-APR epoch/buffer — APR0 mode is a supported configuration exercised by the test suite. The trigger condition (manager setting a non-zero APR while a receipt is pending) is an ordinary operations action, not adversarial privileged behavior; the contract gives no warning that an APR update will brick the next `stopEpoch`. Cost to the attacker is gas plus temporarily parking principal.

### Recommendation
In `prepareStopEpochWithApr0`, settle the APR0 bucket at the pending rate rather than reverting when `unscaledApr != 0`: write `apr0RateByEpoch[epochNumber]` (0 if no override interest), zero `apr0TotalPrincipal`, and let `_settleApr0` pay principal-only on claim — APR0 requesters already received their terms at request time. Alternatively, keep the revert but also gate `setApr`/`setAprsWithBuffer` so a non-zero APR cannot be set while `apr0TotalPrincipal != 0`, making the DoS impossible instead of recoverable-only.

### Proof of Concept
```solidity
// Foundry fork test, modeled on test/foundry/IdleCreditVault.t.sol APR0 tests
function testApr0PendingReceiptBlocksStopEpochAfterAprRaise() external {
    // zero-APR configuration (supported mode)
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    idleCDO.depositAA(10_000 * ONE_SCALE);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);

    // stop epoch 0 with APR still 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _forceLastEpochAprToZero(); // emulate lastEpochApr == 0 so request is normal-path APR0

    // attacker: unprivileged user files APR0 withdraw request during buffer
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // honest manager raises APR for the upcoming epoch (routine admin call)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // rollover now permanently reverts: APR0 bucket + non-zero APR = "unsupported algorithm"
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch() +
        IdleCreditVault(address(strategy)).pendingWithdraws());
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // same for close-pool mode: pool cannot be closed either
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 1);

    // epochNumber stuck -> attacker's and all other receipts are unclaimable
    assertEq(IdleCreditVault(address(strategy)).epochNumber(), 1);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-330)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
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

**File:** contracts/IdleCDOEpochVariant.sol (L330-364)
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
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```
