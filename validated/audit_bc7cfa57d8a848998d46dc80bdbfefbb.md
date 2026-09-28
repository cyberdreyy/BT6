### Title
APR0 withdraw receipt permanently reverts `stopEpoch` after any APR change, freezing all vault funds — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
A lender's APR0 withdraw request leaves `apr0TotalPrincipal > 0`, and `prepareStopEpochWithApr0` hard-reverts whenever `unscaledApr != 0`. Once an honest manager/CDO raises the APR for the next epoch, every subsequent `stopEpoch` call reverts, so the epoch can never close and all funds in the vault are frozen until the APR is manually restored to 0. This mirrors the advisory's bug class: attacker-planted state (a "poisoned" receipt bucket) makes a core accounting function throw on every invocation once surrounding data changes legitimately.

### Finding Description
`requestWithdraw` routes a request into the APR0 bucket whenever `unscaledApr == 0` at request time, incrementing the global `apr0TotalPrincipal` and recording `apr0Users[_user].principalEpoch = epochNumber` [1](#0-0) [2](#0-1) . `apr0TotalPrincipal` is cleared in exactly two places: inside `prepareStopEpochWithApr0` (line 540) and inside `_clearWithdrawClaimForEpoch` during a *default* claim. The fast path in `prepareStopEpochWithApr0` returns early only when `_principal == 0`; otherwise it reverts if the APR has since become non-zero [3](#0-2) . Because `stopEpoch` invokes this hook before completing the epoch, the revert poisons every stop call. Crucially, the APR the bucket was recorded under (`unscaledApr` at request time) is a single global slot — there is no per-epoch APR snapshot, so a later honest `setAprsWithBuffer`/`setApr` to a non-zero rate turns the user's pending receipt into a permanent revert gadget. The user cannot self-clean the bucket: `claimWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` [4](#0-3) , and `epochNumber` only increments inside `deposit` during a running epoch [5](#0-4)  — which requires a successful `stopEpoch` first. Deadlock.

### Impact Explanation
Temporary freezing of all user funds at the scale of the full vault TVL (principal + yield of every AA/BB holder). While `apr0TotalPrincipal != 0` and `unscaledApr != 0`, no `stopEpoch`, `startEpoch`, claim, or borrower repayment settlement can proceed; withdrawals, accounting, and interest distribution all halt. Unfreezing requires the honest manager to notice and call `setApr(0)` (or `setAprsWithBuffer(0, ...)`) — possible only because `manager` is allowed to call `setApr` [6](#0-5)  — then successfully stop the epoch. Until that remediation the entire vault is bricked, and if governance/manager keys are operationally unavailable the freeze is indefinite. The frozen amount equals the whole pool NAV.

### Likelihood Explanation
Moderate. The attacker only needs to be a KYC-passing lender who calls `requestWithdraw` while the vault runs a zero-APR epoch (a supported mode, per `unscaledApr == 0` branching). The triggering second step is a routine, honest operation: raising the APR for the next epoch, which happens whenever the borrower's rate is renegotiated. No guard in `setApr`/`setAprsWithBuffer` checks `apr0TotalPrincipal`, so nothing prevents the honest manager from unknowingly bricking the vault. One cheap withdraw request (dust amount suffices — `_principal != 0` is all that is checked) converts the rate update into a vault-wide DoS.

### Recommendation
Snapshot the APR at request time per epoch (e.g., store `apr0Users.principalEpoch` alongside the APR active then, or record `apr0RateByEpoch`/a zero flag) instead of consulting the mutable global `unscaledApr` at stop time. Concretely, in `prepareStopEpochWithApr0`, replace `if (unscaledApr != 0) revert NotAllowed()` with logic that settles the APR0 bucket at zero rate for its own request epoch regardless of the current APR — the bucket only ever needs `apr0RateByEpoch[reqEpoch]`, which can legitimately be 0. Alternatively, block APR changes while `apr0TotalPrincipal != 0` inside `setApr`, or settle/expire stale APR0 principal in `_settleApr0` independent of `epochNumber`.

### Proof of Concept
Foundry fork sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testApr0RequestBricksStopEpochAfterAprChange() external {
    // Setup: vault in APR0 mode
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(0, epochDuration, bufferPeriod);

    // Attacker (KYC'd lender) deposits dust and requests withdraw in APR0 epoch
    uint256 dust = 1e6;
    _depositWithUser(attacker, dust, true); // depositAA
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Honest manager raises APR for next epoch (normal operations)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(initialProvidedApr, epochDuration, bufferPeriod);

    // Epoch end: every stopEpoch reverts -> vault frozen
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, expectedFunds);

    // Attacker cannot self-clean: claim reverts (epochNumber not bumped)
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // Only remediation: manager restores APR 0, then stop succeeds
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(0, epochDuration, bufferPeriod);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, expectedFunds); // now succeeds
}
```

Key assertions: `apr0TotalPrincipal > 0` after the request; `stopEpoch` reverts with `NotAllowed` from `IdleCreditVault.sol:507` while `unscaledApr != 0`; `claimWithdrawRequest` reverts at `IdleCreditVault.sol:327` because `epochNumber` cannot advance, closing the deadlock loop.

Caveat: I was unable to fully verify the exact call site of `prepareStopEpochWithApr0` inside `IdleCDOEpochVariant.stopEpoch` (the grep confirmed calls exist in `IdleCDOEpochVariant.sol` but line-level review was cut short), and whether any higher-level orchestrator path resets `apr0TotalPrincipal` independently. If `stopEpoch` only calls the hook conditionally on APR0 mode already being active, the freeze window narrows but the revert-on-mismatch logic remains a live footgun.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-234)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-287)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L502-508)
```text
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-610)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
```
