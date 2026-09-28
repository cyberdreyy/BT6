### Title
Stale APR0 principal bucket bricks `stopEpoch` once APR leaves the zero regime, permanently freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can create a non-zero `apr0TotalPrincipal` by calling `requestWithdraw` during any epoch where `unscaledApr == 0` (the standard programmable-borrower regime). Because `setAprsWithBuffer`/`setApr` can later set a non-zero APR without clearing or validating the APR0 bucket, the very next `stopEpoch`/`stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0` before `apr0TotalPrincipal` is reset — so the epoch can never be stopped and no default path can be triggered.

### Finding Description
Analogous to CVE-2017-9605 (a variable read on a path where it was never written/reset), `apr0TotalPrincipal` is state written under one APR regime and later read in a path that assumes it is zero. The flow:

- `requestWithdraw` routes to `_requestWithdrawApr0` whenever `unscaledApr == 0` and the pool is not closed, incrementing `apr0TotalPrincipal` and recording the user's `Apr0UserData` (contracts/strategies/idle/IdleCreditVault.sol:285-286, 567-577).
- `apr0TotalPrincipal` is only reset inside `prepareStopEpochWithApr0` at the end of the function (`apr0TotalPrincipal = 0`, line 540).
- But earlier in the same function: `if (unscaledApr != 0) revert NotAllowed();` (lines 506-508). Once APR is non-zero, this revert fires before the reset at line 540, so the stale bucket is never cleared.
- `setAprsWithBuffer`/`setApr` (lines 217-235) impose no check on `apr0TotalPrincipal`, so an honest manager routine APR change (or `_setScaledApr(_newApr)` semantics during any stop attempt sequencing) leaves the vault in the poisoned state.
- `_stopEpoch` in `IdleCDOEpochVariant` calls `_strategy.prepareStopEpochWithApr0(_interest)` unconditionally (contracts/IdleCDOEpochVariant.sol:362), so every stop attempt reverts.
- Since `_handleBorrowerDefault` is only reachable inside `stopEpoch`/`getInstantWithdrawFunds` try/catch flows (lines 398-404, 501-505, 566-573), no default can be declared. `isEpochRunning` stays true, deposits stay paused, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` stay false, and pending withdraw receipts can never be claimed (`_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest`, which requires a successful stop).

Broken invariant: epoch state machine liveness. A stale accounting bucket from a previous APR regime permanently blocks the only function that can advance the epoch.

### Impact Explanation
All LP funds are frozen indefinitely: active tranche holders cannot request or claim withdrawals, pending receipt holders cannot claim, the borrower cannot be forced to repay, and (in programmable mode) ERC4626 liquidity cannot be recalled because `onStopEpoch` is never reached. The freeze persists until the manager happens to set `unscaledApr` back to exactly 0 and stops the epoch; there is no in-contract self-healing and no default escape hatch. Loss = 100% of vault TVL for the duration of the freeze (potentially permanent).

### Likelihood Explanation
- Attacker requirements: any KYC-passing lender (`isWalletAllowed`) with a deposit. No privileged role needed.
- Setup: deposit, call `requestWithdraw` while `unscaledApr == 0` (always true in programmable-borrower pools, and trivially true whenever a pool is configured at 0% APR). Cost is one normal transaction; the request is legitimate and even earns the APR0 interest share.
- Trigger: any subsequent non-zero APR assignment by the honest manager — a routine operation — or simply a `stopEpoch(_newApr != 0, ...)`: note `_setScaledApr(_newApr)` at the end of a successful stop sets APR for the *next* epoch, so the poison only materializes if APR is set non-zero *before* the stop that would settle the APR0 bucket (e.g., via `setAprsWithBuffer`, or `stopEpochWithDuration` calling `setEpochParams`/`_setScaledApr` sequencing in a prior epoch). Once poisoned, every stop reverts.
- Existing guards do not help: the `NotAllowed` revert is itself the bug; `_settleApr0` only runs inside a successful claim path; `requestWithdraw`'s loss-epoch guard is unrelated.

### Recommendation
- In `prepareStopEpochWithApr0`, settle/close the APR0 bucket instead of reverting when `unscaledApr != 0`: treat outstanding APR0 principal as earning zero rate for the closing epoch (settle principal only) or pay it at the stored `apr0RateByEpoch`, then zero `apr0TotalPrincipal` before any revert-prone logic.
- Alternatively/additionally, make `setAprsWithBuffer`/`setApr` revert when `apr0TotalPrincipal != 0`, so the APR regime cannot change while unsettled APR0 receipts exist.
- Also consider sweeping: allow `stopEpoch` to force-settle APR0 principal into `settledPrincipal` when APR changed, preserving user principal while never blocking the epoch.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultApr0Freeze.t.sol
function testApr0StaleBucketFreezesStopEpoch() external {
    // Pool configured with unscaledApr == 0 (programmable borrower mode).
    // 1) Attacker (KYC'd lender) deposits in buffer period
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    uint256 minted = _depositWithUser(attacker, amount);

    // 2) While unscaledApr == 0 and pool not closed, request withdraw.
    //    This routes through _requestWithdrawApr0 and sets apr0TotalPrincipal > 0.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(minted, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // 3) Honest manager starts epoch normally
    _startEpochAndCheckPrices(0);

    // 4) Honest manager sets a non-zero APR for the next epoch
    //    (routine ops call; no check against apr0TotalPrincipal)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(10e18, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    // 5) Epoch ends; borrower is funded and approves repayment
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 6) stopEpoch reverts forever: prepareStopEpochWithApr0 hits
    //    `apr0TotalPrincipal != 0 && unscaledApr != 0` -> NotAllowed
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(10e18, 0);

    // 7) Consequences: isEpochRunning stays true, defaulted stays false,
    //    deposits remain paused, withdraw requests stay closed,
    //    attacker and all other LPs cannot claim anything.
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());
    vm.prank(attacker);
    vm.expectRevert(); // epochNumber <= lastWithdrawRequest
    cdoEpoch.claimWithdrawRequest();
}
```