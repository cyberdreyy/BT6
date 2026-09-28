### Title
Duplicated epoch APR (`lastApr` vs `unscaledApr`) in `IdleCreditVault` diverges when updated via `setApr` instead of `setAprs`/`setAprsWithBuffer` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` keeps two copies of the same epoch APR: `lastApr` (the scaled APR) and `unscaledApr` (the raw APR). `setAprs` and `setAprsWithBuffer` update both, but the public `setApr` — which is a documented entry point for the manager — updates only `lastApr`. Downstream logic in `IdleCDOEpochVariant.requestWithdraw` and `IdleCreditVault.requestWithdraw`/`prepareStopEpochWithApr0` reads `unscaledApr`, so an APR change applied through `setApr` leaves withdraw-request routing, the APR0 settlement guard, and the instant-withdraw APR-drop check operating on a stale value — the same "same parameter in two places, updated through an alternate authorized path" bug class as the L2ECO `rebase`/`L2ECOBridge` inflation-multiplier report. [1](#0-0) [2](#0-1) 

### Finding Description
`IdleCreditVault` stores the APR twice: `lastApr` ("latest saved apr, already scaled to include the buffer period") and `unscaledApr` [1](#0-0) [2](#0-1) . Three setters exist:

- `setAprs` sets `unscaledApr` then calls `setApr` [3](#0-2) 
- `setAprsWithBuffer` sets `unscaledApr` then calls `setApr` with the buffer-scaled value [4](#0-3) 
- `setApr` — callable by the CDO **or the manager** — writes only `lastApr`, and the NatSpec explicitly documents the manager calling it directly: "If manager manually set apr from here it will not be scaled to include the buffer period" [5](#0-4) 

Consumers of the two copies differ:

- `requestWithdraw` routes a request into the APR0 bucket only when `unscaledApr == 0` [6](#0-5) 
- `prepareStopEpochWithApr0` reverts `NotAllowed` if `unscaledApr != 0` while `apr0TotalPrincipal != 0` [7](#0-6) 
- `IdleCDOEpochVariant.requestWithdraw` compares the stored `lastEpochApr` against `creditVault.unscaledApr()` to decide whether the APR dropped enough to allow an instant-withdraw request, with the comment "we compare unscaled aprs" [8](#0-7) 
- `lastEpochApr` is snapshotted from `unscaledApr` at each `stopEpoch` [9](#0-8) 

An unprivileged user cannot call any setter, but the divergent state is reachable through honest, documented manager/owner actions (`setApr` is exercised this way even in the repo's own test setup) [10](#0-9) . Once diverged, any user can trigger the wrong branch.

Concretely:

1. **Instant-withdraw gate desync.** The epoch APR is lowered via `setApr` (a supported operation: the manager may want to cut yield mid-cycle, and `lastApr`/`getApr()` reflect the new rate). `unscaledApr` still holds the old higher value. The check `lastEpochApr > currentApr + instantWithdrawAprDelta` compares a stale `unscaledApr`, so users who are entitled to an instant exit after the real APR drop are denied it (or, if the APR is raised via `setApr`, `unscaledApr` stays low and `lastEpochApr > unscaledApr + delta` wrongly enables instant exits for users who should not have them). [11](#0-10) 
2. **APR0 routing desync.** With `unscaledApr` stale-0 while `lastApr` is nonzero (or vice versa), withdraw requests are pushed into `_requestWithdrawApr0`/`apr0Users` when they should be normal per-epoch receipts, or normal receipts when they should accrue via `apr0RateByEpoch`. An APR0-routed receipt embeds the `_underlyings` amount computed by `_calcInterestWithdrawRequest` (which already contains fixed interest) and then accrues a *second* pro-rata share via `apr0RateByEpoch` at `prepareStopEpochWithApr0`, inflating `pendingWithdraws` that `stopEpoch` pulls from the borrower — a direct overpayment drained from the borrower's transferred funds, leaving remaining LPs' claims underfunded. [12](#0-11) [13](#0-12) 

### Impact Explanation
The broken invariant is fair mint/burn and solvency of pending receipts: withdraw receipts are minted and funded under one APR regime while the interest actually owed by the borrower (`expectedEpochInterest`, funded via `getFundsFromBorrower`) follows the other copy. Quantified loss is the APR delta `|lastApr-derived rate − unscaledApr|` applied to the requesting principal over `epochDuration` — i.e., the wrong "multiplier" applied to transfers, mirroring the L2ECOBridge deposit/withdraw mismatch in the external report. Wrongly-enabled instant withdrawals let users exit at par and force `collectInstantWithdrawFunds` to pull borrower liquidity ahead of normal receipts; wrongly-routed APR0 requests double-count interest and inflate `pendingWithdraws` pulled from the borrower at `stopEpoch`. Wrongly-*blocked* instant withdrawals after a genuine APR cut temporarily freeze users' promised exit path for the remainder of the buffer/epoch. [14](#0-13) [15](#0-14) 

### Likelihood Explanation
Requires an honest privileged action (manager or CDO calling `setApr` directly rather than `setAprs`/`setAprsWithBuffer`), which is an explicitly documented and tested usage pattern — the NatSpec acknowledges the manager path and the test harness uses `cv.setApr(...)` to set a manually-scaled APR [5](#0-4) [10](#0-9) . Per the threat model, privileged actors are honest but their ordinary calls can be sequenced around; the exploit itself (calling `requestWithdraw` while the two copies diverge) is fully unprivileged. Likelihood is moderate: divergence persists only until the next `stopEpoch`/`setAprsWithBuffer` resyncs both values, so the window is a single buffer/epoch period, and the largest losses require an APR0 or instant-withdraw-enabled configuration.

### Recommendation
Same fix as the external report: keep a single source of truth. Either remove `unscaledApr` and derive it from `lastApr` (store the buffer factor, or expose `getApr`/`unscaledApr` computed from one stored value), or make `setApr` private so the only external entry points (`setAprs`, `setAprsWithBuffer`, and the CDO's `_setScaledApr`) always write both fields atomically. At minimum, have `setApr` also update `unscaledApr` consistently and revert when called by the manager in a way that would desync the two. [16](#0-15) 

### Proof of Concept
Foundry fork test (skeleton, based on `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testAprDesyncRoutesWithdrawWrongly() external {
    // Setup: epoch running, unscaledApr = lastApr = initialProvidedApr > 0,
    // instant withdrawals enabled via setInstantWithdrawParams.

    uint256 amount = 10_000 * oneScale;
    idleCDO.depositAA(amount); // attacker = KYC'd AA lender

    // Honest manager lowers the pool APR mid-epoch via the documented direct path.
    // setApr writes lastApr only; unscaledApr stays at the OLD (higher) value.
    vm.prank(manager);
    IdleCreditVault(strategy).setApr(scaledLowerApr);

    // Divergence check: the two "copies" of the same APR disagree.
    assert(IdleCreditVault(strategy).getApr() == scaledLowerApr);
    assert(IdleCreditVault(strategy).unscaledApr() == initialProvidedApr);

    // EXPLOIT A (instant-withdraw gate): lastEpochApr vs STALE unscaledApr
    // evaluates as "no APR drop", so the attacker is wrongly denied the
    // instant exit the real (lastApr) drop should have enabled — or, if the
    // direction is reversed (setApr raise with stale-low unscaledApr), the
    // attacker wrongly obtains an instant withdrawal and pulls borrower
    // liquidity via collectInstantWithdrawFunds ahead of normal receipts.
    cdoEpoch.withdrawAA(amount); // lands on requestInstantWithdraw branch iff gate passes

    // EXPLOIT B (APR0 routing): repeat with an epoch where unscaledApr is
    // left stale-0 via a prior apr0 epoch + setApr(nonZeroScaled).
    // requestWithdraw hits `unscaledApr == 0` and routes into _requestWithdrawApr0;
    // the receipt already contains fixed interest from _calcInterestWithdrawRequest,
    // then apr0RateByEpoch pays a SECOND pro-rata interest share at stopEpoch,
    // inflating pendingWithdraws pulled from the borrower at stopEpoch.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // pendingWithdraws inflated by double-counted interest

    // Attacker claims over-funded receipt; residual LPs / borrower fund the excess.
    cdoEpoch.claimWithdrawRequest();
}
```

Caveat: `_getStrategyApr()`'s exact backing field (`unscaledApr` vs `getApr`/`lastApr`) was not fully traced; if interest math reads `unscaledApr`, the double-count in Exploit B is even larger because receipts embed interest at the stale rate while `prepareStopEpochWithApr0` also settles the APR0 bucket. The routing/gate desync (Exploit A and the `unscaledApr == 0` branch selection) is independent of that and stands on the cited code alone.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L50-51)
```text
  /// @notice latest saved apr, already scaled to include the buffer period
  uint256 public lastApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L72-73)
```text
  /// @notice unscaled apr
  uint256 public unscaledApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-210)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-220)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L222-235)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L505-508)
```text
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-537)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-410)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L468-471)
```text
      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);
```

**File:** contracts/IdleCDOEpochVariant.sol (L542-546)
```text
  /// @notice Set the scaled apr for the next epoch
  /// @param _newApr New apr to set for the next epoch
  function _setScaledApr(uint256 _newApr) internal {
    IdleCreditVault(strategy).setAprsWithBuffer(_newApr, epochDuration, bufferPeriod);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-573)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L759-769)
```text
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

**File:** test/foundry/IdleCreditVault.t.sol (L113-116)
```text
    // scaled by the buffer period
    vm.startPrank(cv.manager());
    cv.setApr(_scaleAprWithBuffer(initialProvidedApr));
    vm.stopPrank();
```
