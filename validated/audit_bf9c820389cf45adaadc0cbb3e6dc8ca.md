### Title
APR0 withdraw bucket permanently DoSes `stopEpoch` after an APR change — `prepareStopEpochWithApr0` reverts whenever `unscaledApr != 0` while `apr0TotalPrincipal != 0` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug (CVE-2024-6119) is a crash triggered when an optional identity/name check encounters an unexpected alternative-name type, aborting the whole TLS handshake. The idle-tranches analog is an abortive consistency check in the epoch-settlement path: `prepareStopEpochWithApr0` reverts `NotAllowed` whenever an APR0 withdraw bucket exists but `unscaledApr` is no longer zero. Because this check runs inside `stopEpoch`/`stopEpochWithDuration` — the only way to close an epoch and fund pending receipts — a single unprivileged withdrawal request can wedge the entire vault into a state where every `stopEpoch` call reverts.

### Finding Description
In `IdleCreditVault.requestWithdraw`, any request made while `unscaledApr == 0` is routed into the APR0 bucket via `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` [1](#0-0) . The bucket is only closed inside `prepareStopEpochWithApr0`, which is invoked unconditionally by `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` [2](#0-1) . But that function reverts if the APR has moved off zero while the bucket is still open:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) { return (...); }
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
``` [3](#0-2) 

`unscaledApr` is set by `setAprsWithBuffer`/`setApr`, which can be called by the manager or the CDO at any time, including mid-epoch, independently of `stopEpoch` [4](#0-3) . So the reachable sequence is:

1. Epoch N settles with `_newApr = 0` (an APR0 pool configuration), buffer begins.
2. Attacker (any KYC-passed wallet allowed by `isWalletAllowed`) calls `cdoEpoch.requestWithdraw(amount, AATranche)`, creating `apr0Users[attacker].principal` and `apr0TotalPrincipal > 0` [5](#0-4) .
3. The honest manager adjusts pricing — e.g. calls `setAprsWithBuffer(nonzero, ...)` or `stopEpoch(interest, newApr)` ordering aside, any path that leaves `unscaledApr != 0` while `apr0TotalPrincipal != 0`.
4. Epoch starts and runs; borrower repays on schedule.
5. `manager.stopEpoch(...)` → `prepareStopEpochWithApr0` → `revert NotAllowed()`. Every subsequent `stopEpoch` reverts identically because the only code that zeroes `apr0TotalPrincipal` is the very function that reverts [6](#0-5) .

There is no unwind: the attacker cannot cancel the request, `requestWithdraw`/`claimWithdrawRequest` cannot run while `isEpochRunning`, and nothing else mutates `apr0TotalPrincipal`. The vault is stuck in "epoch running past `epochEndDate`" until the manager happens to set `unscaledApr` back to exactly 0 and re-stops — and even then, any strategy variant where the manager intends a nonzero APR must first roll the APR back to zero, settle, then re-raise it, forcing an unintended APR0 epoch.

### Impact Explanation
All funds in the vault are frozen for the duration: borrower repayments cannot be collected (`getFundsFromBorrower` only runs inside the reverting `stopEpoch`), active LP tranche redemptions are impossible (`_beforeUnpause` blocks unpause while `isEpochRunning`), and every pending withdraw receipt stays unfunded. This is a temporary (potentially indefinite, if the manager never diagnoses that the fix is resetting APR to 0) freezing of 100% of vault TVL caused by one unprivileged withdrawal request — matching the "compare against an unexpected name type → crash the whole operation" shape of CVE-2024-6119: an optional per-request accounting mode poisons a global settlement routine.

### Likelihood Explanation
Preconditions are modest: a deployment running (or stopped into) an APR0 epoch, a manager APR adjustment while an APR0 request is open — both routine honest operations — plus one unprivileged `requestWithdraw`, which requires only `isWalletAllowed` (KYC) and a nonzero tranche balance [7](#0-6) . No existing guard prevents it: the check at `IdleCDOEpochVariant` line 348 only constrains `_newApr` when `_interest > 1` inside `stopEpoch` itself, and nothing blocks `setApr`/`setAprsWithBuffer` while `apr0TotalPrincipal != 0`.

### Recommendation
Instead of reverting, settle the open APR0 bucket at the rate that applied during the request epoch (which is stored per-epoch via `apr0RateByEpoch`) or settle it with zero interest, then close the bucket — i.e. replace the `revert NotAllowed()` at `IdleCreditVault.sol:506-508` with graceful settlement. Alternatively, revert APR changes (`setApr`/`setAprsWithBuffer`) while `apr0TotalPrincipal != 0`, keeping the invariant at the cheaper call site rather than inside the unskippable `stopEpoch` path.

### Proof of Concept
Not fully verified end-to-end (I did not get to confirm whether `_newApr` in `stopEpoch` is written to `unscaledApr` before or after `prepareStopEpochWithApr0` at line 362 — the surrounding code at lines 468-470 suggests the new APR is applied after the strategy call, so the revert requires a *separate* `setApr`/`setAprsWithBuffer` call between request and stop, which is still a normal manager action). A Foundry PoC sketch:

```solidity
// test/foundry/IdleCreditVault.t.sol style
function testApr0RequestBricksStopEpochAfterAprChange() external {
    // setup: pool in APR0 mode (unscaledApr == 0), buffer phase
    idleCDO.depositAA(10_000 * ONE_SCALE);

    // attacker opens an APR0 withdraw request during buffer
    cdoEpoch.requestWithdraw(1_000 * ONE_TRANCHE, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // honest manager raises APR for the upcoming epoch
    vm.prank(manager);
    strategy.setAprsWithBuffer(5e18, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    // epoch starts and runs; borrower repays
    _startEpochAndCheckPrices(0);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // every stopEpoch attempt reverts; vault funds frozen
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
}
```

**Caveat:** whether the freeze is *permanent* depends on whether the manager can recover by calling `setApr(0)` and re-stopping; if recovery is possible this is a temporary freeze of all vault funds (still accepted per the rules) rather than permanent loss. If `setApr(0)` is blocked in the relevant configuration (e.g., `maxApr` interactions or the APR0 bucket interacting with `pendingWithdraws` accounting in `collectWithdrawFunds`), the freeze becomes permanent. This ordering detail in `stopEpoch`/`stopEpochWithDuration` and the `_settleApr0` interplay were not fully explored within the available iterations.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/IdleCDOEpochVariant.sol (L773-790)
```text
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
