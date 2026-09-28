### Title
APR0 withdraw request permanently bricks `stopEpoch` after an APR change, freezing all pool funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0()` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can open an APR0 withdraw request while the APR is 0; once the (honest) manager later sets a non-zero APR, the APR0 bucket can never be cleared, every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts, and the pool's funds are frozen permanently.

### Finding Description
The epoch stop flow in `IdleCDOEpochVariant._stopEpoch` calls `prepareStopEpochWithApr0` on the strategy before pulling borrower funds [1](#0-0) . In `prepareStopEpochWithApr0`, when `apr0TotalPrincipal` is non-zero, the function reverts if `unscaledApr != 0` [2](#0-1) . The only code that resets `apr0TotalPrincipal` to 0 is at the end of that same function [3](#0-2) , which is unreachable after the revert — so the state is terminal.

The attacker seeds the bad state via `requestWithdraw` → `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` whenever `unscaledApr == 0` [4](#0-3) [5](#0-4) . User-side clearing paths (`_settleApr0`, `claimWithdrawRequest`) never decrease `apr0TotalPrincipal`; only the default-claim path (`_isClearingApr0`) does, which requires a borrower default that already freezes the pool anyway [6](#0-5) .

This is the credit-vault analog of a null-deref crash: parsing the stored APR0 epoch state (`apr0TotalPrincipal`) against a now-inconsistent `unscaledApr` produces a fatal, unrecoverable revert in the epoch state machine.

### Impact Explanation
Permanent freezing of all funds. `stopEpoch`/`stopEpochWithDuration` is the only way to end a running epoch, fund pending withdraw receipts, and re-enable claims. With it permanently reverting, pending withdraw receipts can never be funded, `epochNumber` never advances, and no lender can claim — the entire pool TVL plus accrued interest is locked. Attacker cost is one deposit + one `requestWithdraw` during an honest zero-APR configuration window.

### Likelihood Explanation
Requires: (a) epoch APR is 0 at some point (a supported, tested mode — APR0 flow exists specifically for it), and (b) the manager later sets a non-zero APR via `stopEpochWithDuration`/`setAprs` before the next successful stop, or `unscaledApr` is non-zero while `apr0TotalPrincipal` persists. The revert triggers on the manager's very next stop call, independent of attacker timing after setup. No privileged misbehavior required — the attacker only uses the public `requestWithdraw` path.

### Recommendation
Instead of reverting when `unscaledApr != 0` with open APR0 principal, settle the bucket defensively: either (a) treat the open APR0 principal as a normal funded pending receipt (migrate `apr0TotalPrincipal` into `pendingWithdraws`/per-epoch buckets and zero it), or (b) compute `apr0RateByEpoch[epochNumber]` and clear `apr0TotalPrincipal` before the `unscaledApr` check so the revert can never become terminal. At minimum, zero `apr0TotalPrincipal` before reverting so a single inconsistent state cannot permanently brick `stopEpoch`.

### Proof of Concept
```solidity
// Foundry fork test — attacker is `user` (KYC'd AA holder). Owner/manager act honestly.
function testApr0StopEpochPermanentDoS() external {
    // 1. Honest config: manager sets APR to 0 (APR0 mode, supported flow)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // unscaledApr = 0

    // 2. Attacker deposits and requests withdraw while APR is 0
    deal(defaultUnderlying, user, 10_000e6);
    vm.startPrank(user);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), type(uint256).max);
    idleCDO.depositAA(10_000e6);
    _startEpochAndStop(); // epoch advances; APR still 0
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // apr0TotalPrincipal > 0
    vm.stopPrank();
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // 3. Honest manager raises APR for the next epoch (normal operation)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 0); // unscaledApr != 0

    // 4. Attacker does nothing. Epoch ends; manager calls stopEpoch.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, type(uint128).max);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), type(uint256).max);

    // 5. stopEpoch reverts in prepareStopEpochWithApr0 — and always will,
    //    because apr0TotalPrincipal can only be cleared past the revert.
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Repeat forever: funds permanently frozen, pending receipts unfundable.
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L361-364)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-830)
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
```
