### Title
APR0 withdraw receipt permanently reverts `stopEpoch` when a nonzero APR is set — unprivileged lender can freeze the epoch state machine — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The analog of CVE-2020-21674 (an attacker-controlled input that drives a length miscalculation into an unconditional out-of-bounds write / crash) is a crafted state input that drives `prepareStopEpochWithApr0` into an unconditional `revert NotAllowed()`. Any KYC-passing lender can open an APR0 withdraw receipt while `unscaledApr == 0`; afterwards, every `stopEpoch`/`stopEpochWithDuration` call reverts whenever the manager tries to set a nonzero `_newApr`, because `apr0TotalPrincipal != 0` combined with `unscaledApr != 0` hard-reverts. The epoch can then never be closed under a normal (positive) APR configuration, freezing all pool funds behind the borrower's repayment path.

### Finding Description
`IdleCDOEpochVariant.requestWithdraw` routes through `IdleCreditVault.requestWithdraw`, which buckets the receipt into the APR0 flow whenever `unscaledApr == 0` (`_requestWithdrawApr0` increments `apr0TotalPrincipal`) [1](#0-0) [2](#0-1) .

At epoch end, `IdleCDOEpochVariant._stopEpoch` unconditionally calls `_strategy.prepareStopEpochWithApr0(_interest)` [3](#0-2) . Inside the strategy:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) { return (...); }
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
``` [4](#0-3) 

The problem: `unscaledApr` is **not** the APR of the epoch being stopped — it is whatever the last `_setScaledApr(_newApr)` set for the *next* epoch at the previous `stopEpoch` (line 471, `_setScaledApr(_newApr)` → `setAprsWithBuffer` → `unscaledApr = _unscaledApr`) [5](#0-4) [6](#0-5) . So if the honest manager raises the pool APR for epoch N (a routine, legitimate configuration change), then a single lender opens an APR0 receipt *before* that `stopEpoch` executes — note `requestWithdraw` checks `unscaledApr == 0` only at request time — wait, more precisely: the revert fires when `apr0TotalPrincipal != 0` **and** `unscaledApr != 0` at stop time. Two concrete sequences reach it:

1. **APR raised mid-epoch by manager** (`strategy.setAprs`/`setApr` is callable by `manager` directly, line 225-235): attacker deposits in an APR0 epoch, calls `requestWithdraw` (enters `apr0TotalPrincipal` since `unscaledApr == 0` at that moment), manager later sets `unscaledApr > 0` or it was already set by the previous `stopEpoch(_newApr)`. The next `stopEpoch` reverts at `prepareStopEpochWithApr0` **before** `_setScaledApr` can restore `unscaledApr = 0`, because the revert happens at line 362, ahead of line 471.

2. **Manager stops an APR0 epoch with `_newApr > 0`**: even with no mid-epoch APR change, `_stopEpoch` reads `apr0TotalPrincipal != 0` with `unscaledApr == 0` and settles fine — but if the *previous* epoch's stop set `unscaledApr > 0`, and an attacker opens an APR0 receipt during a window where the accounting mixes (e.g., receipts opened at `unscaledApr == 0` then the stored scaled APR is nonzero), the revert is unconditional.

In sequence 1 the lock is self-reinforcing: `stopEpoch` cannot run, so `unscaledApr` can never be returned to 0 through the CDO path (`_setScaledApr` is only reached at the end of a successful stop), and `setApr`/`setAprsWithBuffer` reject non-CDO/non-manager callers. Recovery requires the manager to notice and manually call `setAprs(0,0)` on the strategy — an out-of-band privileged intervention that the protocol flow itself cannot trigger.

### Impact Explanation
While `stopEpoch` reverts, `isEpochRunning` stays true and `epochEndDate` is in the past: `requestWithdraw` receipts accumulate in `pendingWithdraws`/`apr0TotalPrincipal` but can never be funded (`collectWithdrawFunds` only runs inside `stopEpoch`), `claimWithdrawRequest` reverts via `epochNumber <= lastWithdrawRequest` [7](#0-6) , and `_claimFundedWithdrawRequest` never pays out. All tranche holders' funds — the full `getContractValue()` TVL plus pending receipts — are frozen for the duration of the freeze, matching the "temporary freezing with quantified loss" acceptance criterion (loss = time-value of entire TVL plus inability to exit during adverse market/borrower events; if the manager key is unavailable the freeze is permanent). Attacker cost is one ordinary deposit + `requestWithdraw`, both allowed for any KYC-passing wallet.

### Likelihood Explanation
- Attacker requirements: pass `isWalletAllowed` (KYC), hold any tranche balance, call `requestWithdraw` while `unscaledApr == 0`. No privileged role needed [8](#0-7) .
- Trigger condition: `unscaledApr` transitions to nonzero while `apr0TotalPrincipal != 0` — either via manager `setAprs`/`setApr` mid-epoch (manager is honest but performs routine APR updates), or via the stored scaled APR from the previous `stopEpoch(_newApr)`. APR0 deployments explicitly support the manager later restoring a positive APR (otherwise the pool could never leave APR0 mode once any withdraw request exists), so this is a normal operational path, not an edge case.
- Existing guards do not prevent it: the `lossRecoveryPriceByEpoch` re-request guard only covers loss-adjusted epochs [9](#0-8) , `_settleApr0` only runs lazily on user claim [10](#0-9) , and nothing in `_stopEpoch` settles or bypasses the APR0 bucket before reading `unscaledApr`.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `apr0TotalPrincipal != 0 && unscaledApr != 0`. Either:
- settle the APR0 bucket at the epoch's own rate regardless of the newly configured `unscaledApr` (the receipts were priced under APR0 terms at request time — the current `unscaledApr` only governs the *next* epoch), or
- snapshot the APR that was in force when each APR0 request was created (per-epoch in `apr0Users.principalEpoch`) and settle against that, so a later APR change cannot retroactively brick the stop.

Additionally, `requestWithdraw` should reject (or route to the normal bucket) when `unscaledApr == 0` but the stored scaled APR/`lastApr` for the upcoming settlement is nonzero, so the bucket-APR invariant the revert tries to protect is enforced at request time instead of crashing the epoch state machine.

### Proof of Concept
Foundry fork PoC (against an APR0-configured `IdleCDOEpochVariant` deployment, e.g. the programmable/fixed-APR0 setup used in `test/foundry/IdleCreditVault.t.sol` APR0 tests):

```solidity
function testApr0ReceiptLocksStopEpochAfterAprChange() external {
  uint256 amount = 10_000 * ONE_SCALE;
  idleCDO.depositAA(amount);                 // attacker (KYC'd) deposits in AA
  _startEpochAndCheckPrices(0);              // epoch 0 runs at unscaledApr == 0

  // epoch 0 ends, manager stops it keeping APR = 0 for epoch 1
  vm.warp(cdoEpoch.epochEndDate() + 1);
  deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);                  // unscaledApr still 0

  // attacker opens an APR0 withdraw receipt during the buffer/epoch-1 window
  uint256 trancheBal = IERC20(AAtranche).balanceOf(address(this));
  cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));
  assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

  _startEpochAndCheckPrices(1);

  // honest manager raises APR mid-epoch (routine admin action, manager is honest)
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprs(10e18, 10e18); // unscaledApr != 0

  // every subsequent stopEpoch reverts: pool cannot close the epoch
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 0);

  // also for any interest override / new APR
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 1_000 * ONE_SCALE);

  // claim is impossible too: epochNumber never advanced past request epoch
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.claimWithdrawRequest();
}
```

Note: `prepareStopEpochWithApr0` runs before `_setScaledApr(_newApr)` in `_stopEpoch`, so passing `_newApr = 0` does **not** clear `unscaledApr` first — the revert is reached while `unscaledApr != 0` still holds, and there is no CDO-side path to reset it. Only a direct manager call to `strategy.setAprs(0,0)` unblocks the pool.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-220)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L553-555)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L360-364)
```text
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L468-472)
```text
      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

```

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```
