### Title
APR-0 withdraw bucket permanently bricks `stopEpoch` after an APR change - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` unconditionally reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can leave a nonzero `apr0TotalPrincipal` by calling `requestWithdraw` during a zero-APR buffer phase. When the (honest) manager later raises the APR via `setAprs`/`setAprsWithBuffer`, every subsequent `stopEpoch`/`stopEpochWithDuration` call on `IdleCDOEpochVariant` reverts before the bucket can be cleared, permanently freezing the running epoch and all vault funds.

### Finding Description
The analog of CVE-2017-16646 (an attacker-controlled device descriptor field that is never validated and later dereferenced, crashing the kernel) is an attacker-controlled accounting field — `apr0TotalPrincipal` — that is never reconciled with a later state change (`unscaledApr`) and later crashes the critical epoch-transition path.

The lifecycle:

1. `IdleCreditVault.requestWithdraw` routes a request into the APR-0 bucket when `unscaledApr == 0`, incrementing `apr0TotalPrincipal` and `apr0Users[_user].principal` [1](#0-0) [2](#0-1) .
2. `prepareStopEpochWithApr0`, called unconditionally inside `_stopEpoch`, reverts if the bucket is non-empty while `unscaledApr != 0` [3](#0-2) .
3. `apr0TotalPrincipal = 0` is only reached at the end of `prepareStopEpochWithApr0`, so the revert makes the bucket impossible to clear while APR is nonzero [4](#0-3) .
4. `_settleApr0` (the other path that could drain the bucket) requires `principalEpoch < epochNumber`, but `epochNumber` only increments inside `deposit`, which runs inside the same `stopEpoch` that reverts — a circular dependency [5](#0-4) [6](#0-5) .
5. `setAprs` is callable by the honest manager at any time with no check on `apr0TotalPrincipal` [7](#0-6) . Neither `requestWithdraw` nor `setAprs` guards this combination.

The only escape is for the manager to set `unscaledApr` back to 0, run `stopEpoch` (which clears the bucket), and then set the APR again — meaning the vault can never permanently leave the zero-APR state while an APR-0 request exists. If the intended new rate is nonzero, the freeze is permanent.

### Impact Explanation
Permanent freezing of all funds. With `isEpochRunning == true` and `stopEpoch` always reverting: `epochEndDate` has passed but the epoch cannot close; `allowAAWithdrawRequest`/`allowBBWithdrawRequest` remain `false`; `claimWithdrawRequest` reverts because `epochNumber <= lastWithdrawRequest[user]`; deposits stay paused. The attacker's cost is a single ordinary `requestWithdraw` during a zero-APR buffer (a normal, allowed user action — no flags needed, since `allowAAWithdrawRequest` is open between epochs). The damage scales with entire TVL locked in the epoch.

### Likelihood Explanation
Zero-APR epochs are a supported first-class mode (programmable borrowers always run APR 0, and `setAprs(0,0)` is exercised in tests). A rate change from 0 to a positive APR is a routine, honest manager action — e.g., re-pricing a borrower facility after a promotional/zero-rate epoch. Any lender withdrawing during the zero-rate buffer creates the landmine. The attacker needs nothing more than a KYC-passing wallet and a tranche balance.

### Recommendation
In `prepareStopEpochWithApr0`, handle the APR-transition case instead of reverting: settle the outstanding APR-0 bucket at the old zero rate (set `apr0RateByEpoch[epochNumber]` accordingly, move `apr0TotalPrincipal` into per-user `settledPrincipal`, then zero the bucket) so the epoch can close under the new APR. Alternatively, block the unsafe state transition earlier — revert in `setAprs`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0 && _unscaledApr != 0` — so the manager is forced to stop the epoch at APR 0 first, at which point requests settle and the transition is safe.

### Proof of Concept
Foundry test sketch (fork/local, modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_forceLastEpochAprToZero`):

```solidity
function testApr0BucketBricksStopEpochAfterAprChange() external {
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    // Run epoch 0 at APR 0 (programmable-borrower-style zero rate)
    vm.prank(manager);
    cv.setAprs(0, 0);
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Buffer phase at APR 0: unprivileged lender requests withdraw -> apr0 bucket > 0
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(cv.apr0TotalPrincipal(), 0);

    // Honest manager raises APR for the next epoch
    uint256 newApr = 1e18; // 1%
    uint256 scaled = newApr * (cdoEpoch.epochDuration() + cdoEpoch.bufferPeriod()) / cdoEpoch.epochDuration();
    vm.prank(manager);
    cv.setAprs(newApr, scaled);

    // Start epoch 1, warp to end, manager tries to stop -> always reverts
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Bucket can never clear while APR != 0: epochNumber cannot advance,
    // _settleApr0 stalls, claimWithdrawRequest reverts, deposits stay paused.
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
}
```

Caveat: this analysis is based on the indexed contract sources; I could not verify against a live fork, but the revert path and the circular `epochNumber`/`apr0TotalPrincipal` dependency are directly visible in `prepareStopEpochWithApr0` (IdleCreditVault.sol:499-541), `_settleApr0` (:545-565), and `_stopEpoch` (IdleCDOEpochVariant.sol:330-505).

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-210)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-287)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L537-540)
```text
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-610)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
```
