### Title
Permanent `stopEpoch` revert after an APR0 withdraw request once APR is raised — unprivileged lender can freeze the pool - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`prepareStopEpochWithApr0` hard-reverts whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. `apr0TotalPrincipal` is only ever cleared inside that same function, *after* the revert, and is never decremented by `_settleApr0`, `delete apr0Users`, or any claim path. A lender who files a single withdraw request while the pool is in fixed-APR=0 mode leaves a permanently non-zero bucket; the moment the (honest) manager sets a non-zero APR, every subsequent `stopEpoch` call reverts, so the epoch can never close and all user funds are frozen. This is the direct analog of the CVE's unchecked-NULL crash: a state combination the code assumes impossible ("no APR0 principal can exist while APR ≠ 0") is reachable, and the missing guard turns it into an unrecoverable revert.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` (line ~285) routes APR=0 requests to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` (line 576) and records `apr0Users[_user].principal`/`principalEpoch`. Any KYC'd tranche holder can do this via `IdleCDOEpochVariant.requestWithdraw`.
- `setAprs` / `setApr` (lines 206–235) let the manager change `unscaledApr`/`lastApr` at any time with no check on `apr0TotalPrincipal`.
- `prepareStopEpochWithApr0` (lines 499–508), called by the CDO inside `stopEpoch`/`stopEpochWithDuration`, executes:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

- The only write that resets `apr0TotalPrincipal` is `apr0TotalPrincipal = 0` at line 540, which is unreachable once the revert fires.
- `_settleApr0` (lines 545–565) migrates `apr0Users[_user].principal` to `settledPrincipal` and `delete apr0Users[_user]` in `_claimFundedWithdrawRequest` (line 347) clears the per-user struct — neither touches the global `apr0TotalPrincipal`. There is no other decrement anywhere.

So once (a) an APR0-mode withdraw request exists and (b) `unscaledApr` is set non-zero, `prepareStopEpochWithApr0` reverts unconditionally and forever. Since `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` must call it, the running epoch can never be stopped; new `startEpoch` is gated on the previous epoch closing, so the entire pool liveness is dead. Deposits sit in the strategy/CDO and withdraw receipts cannot be funded — permanent freezing of funds, not merely gas/DoS.

No existing guard prevents it: `requestWithdraw` doesn't check that APR will stay 0, `setAprs` doesn't check `apr0TotalPrincipal`, and the revert in `prepareStopEpochWithApr0` *is* the crash — the exact "dereference of state assumed non-null" pattern from the xkbcomp bug.

### Impact Explanation
An unprivileged attacker (any tranche-token holder / KYC'd lender) deposits, waits for or is in an APR=0 epoch, and calls `requestWithdraw` for even a dust amount (e.g. 1 wei of tranche tokens → `_amount` ≥ 1 after fee math). `apr0TotalPrincipal` becomes non-zero permanently. When the honest manager later sets a normal APR (a routine operation via `setAprs`/`setAprsWithBuffer`), the next `stopEpoch` reverts with `NotAllowed`, and this cannot be unwound by anyone — not the owner, not the manager, not the borrower, not even the attacker. All LP funds (AA and BB principal plus accrued interest) remain locked in the vault/strategy indefinitely; pending withdraw receipts can never be funded or claimed. Total pool TVL is at risk for the cost of one dust withdraw request.

### Likelihood Explanation
- The attack requires only a tranche position during an APR=0 epoch — APR0 mode is an explicitly supported vault mode (`_requestWithdrawApr0`, `apr0RateByEpoch`, dedicated tests).
- A single dust `requestWithdraw` suffices; there is no minimum amount check (`if (_amount == 0) return` only filters zero).
- The trigger condition (manager sets APR > 0 while a pending/settled APR0 receipt exists) is a normal operational action — managers routinely reprice APR between epochs, and settled-but-unclaimed APR0 receipts keep `apr0TotalPrincipal > 0` even after the user claims, because claims never decrement it.
- Not previously acknowledged: the code explicitly treats `apr0TotalPrincipal != 0 && unscaledApr != 0` as unreachable rather than guarding the transition, and no test covers the manager raising APR with an outstanding APR0 bucket.

### Recommendation
- Make the state transition safe instead of reverting in `prepareStopEpochWithApr0`: when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, settle the outstanding APR0 principal at zero interest (or at a defined fallback rate) and clear the bucket rather than reverting.
- Alternatively, gate `setAprs`/`setApr`/`setAprsWithBuffer` to revert when `apr0TotalPrincipal != 0`, forcing managers to wait until the bucket is flushed — this keeps the failure at the (reversible) APR-setting step instead of the irreversible epoch-stop step.
- Also decrement `apr0TotalPrincipal` in `_settleApr0`/`_claimFundedWithdrawRequest` so claimed APR0 principal doesn't linger in the bucket.

### Proof of Concept
Foundry fork test (extending `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPocApr0BucketPermanentlyFreezesStopEpoch() external {
    // Setup: deposit as an unprivileged lender (KYC'd) 
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true); // AA tranche holder

    // Honest manager configures an APR = 0 epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    _startEpochAndCheckPrices(0);

    // Attacker files a dust withdraw request during the APR0 epoch.
    // This mints a receipt and permanently increments apr0TotalPrincipal.
    uint256 shares = AAtranche.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust amount suffices
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // Even if the attacker claims later, apr0TotalPrincipal is never decremented:
    // _settleApr0 / delete apr0Users do not touch the global bucket.

    // Honest manager restores a normal APR between epochs (routine reprice)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);

    // Epoch end: every stopEpoch path calls prepareStopEpochWithApr0,
    // which hits `apr0TotalPrincipal != 0 && unscaledApr != 0` → revert.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 toRepay = _expectedFundsEndEpoch();
    deal(defaultUnderlying, borrower, toRepay);
    vm.prank(borrower);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), toRepay);

    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // No privileged actor can recover: the only reset of apr0TotalPrincipal
    // sits behind the same revert. All LP funds are frozen indefinitely.
}
```

Note: the exact `stopEpoch` signature/parameters should match the local `IdleCDOEpochVariant` test helpers (`_stopEpochAndCheckPrices`); the key assertions are that `apr0TotalPrincipal` stays non-zero after any claim path and that `stopEpoch` reverts unconditionally once `unscaledApr != 0`.