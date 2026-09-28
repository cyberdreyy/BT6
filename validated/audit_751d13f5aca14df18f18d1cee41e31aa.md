### Title
Stale APR0 withdraw bucket permanently bricks `stopEpoch` after a mid-epoch APR change, freezing all vault withdrawals - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
An unprivileged KYC'd lender can leave a dust APR=0 withdraw request in `apr0TotalPrincipal`. If the honest manager later raises the APR mid-epoch via `setAprsWithBuffer`, `prepareStopEpochWithApr0` reverts on every subsequent `stopEpoch` call because the bucket is non-empty while `unscaledApr != 0`. The bucket can only be cleared inside that same reverting function, so the epoch cannot close and all user funds are frozen until the manager diagnoses the issue and forces the APR back to 0.

### Finding Description
The external bug is a DoS where a deleted-state artifact remains bound to an identifier and blocks the whole flow. The analog lives in `IdleCreditVault.prepareStopEpochWithApr0`:

- `requestWithdraw` routes requests to `_requestWithdrawApr0` whenever `unscaledApr == 0` at request time, which increases `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `IdleCreditVault.sol:567-570`).
- In `prepareStopEpochWithApr0`, called by `IdleCDOEpochVariant._stopEpoch` before any borrower pull (`IdleCDOEpochVariant.sol:362`), the code reverts unconditionally when `apr0TotalPrincipal != 0 && unscaledApr != 0` (`IdleCreditVault.sol:499-508`).
- `apr0TotalPrincipal` is only reset to 0 later in the same function (`IdleCreditVault.sol:540`), i.e. only reachable if the revert is not hit. `apr0Users[_user].principal` can be settled by `_settleApr0`, but that only reduces per-user data, not `apr0TotalPrincipal`, which is decremented solely here or in `_clearWithdrawClaimForEpoch` (default path only).
- `unscaledApr` is mutable mid-epoch: `setAprsWithBuffer`/`setApr` can be called by the manager at any time (`IdleCreditVault.sol:217-235`), and the comment explicitly supports managers manually setting APR.

So: attacker requests a dust withdrawal during a buffer while `unscaledApr == 0` → epoch starts → manager raises APR mid-epoch (a normal honest action) → every `stopEpoch`/`stopEpochWithDuration` reverts `NotAllowed` at `IdleCreditVault.sol:507`. The state is a catch-22: the bucket cannot be closed without a successful `stopEpoch`, and `stopEpoch` cannot succeed while the bucket is open under a nonzero APR.

### Impact Explanation
While the revert persists, `isEpochRunning` stays true, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` stay false, and all users' deposits and pending withdraw receipts are locked in the vault — a temporary freezing of the entire TVL proportional to how long it takes governance/manager to realize the only recovery is calling `setAprsWithBuffer(0, epochDuration, bufferPeriod)` to force `unscaledApr` back to 0. The attacker's cost is one dust-sized APR0 request (a few units of underlying). All TVL and all unclaimed receipts are hostage to a single stale storage bucket, matching the "deleted/leftover identifier blocks all future auth" shape of the reference bug.

### Likelihood Explanation
Requires an epoch run under `unscaledApr == 0` (used for programmable-borrower and zero-yield configurations, which are first-class supported modes), an attacker with any tranche balance making a dust request during the buffer, and a mid-epoch APR raise by the manager — a plausible operational action since `setApr` is explicitly documented for manual manager use. The attacker fully controls the trigger (the dust request); only the timing of the honest manager's APR change is external. Note the freeze is recoverable once the manager sets the unscaled APR back to 0, so severity is bounded by that response latency.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `apr0TotalPrincipal != 0 && unscaledApr != 0`. Instead either (a) settle the APR0 bucket at the stored `unscaledApr`-independent rate (APR0 requests should accrue 0 by definition, so closing the bucket with `apr0RateByEpoch[epochNumber] = 0` and `apr0TotalPrincipal = 0` is safe), or (b) revert in `setApr`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0` so the inconsistent state can never be entered rather than bricking the epoch-close path.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
// test/foundry/Apr0DustRequestStopEpochDoS.t.sol
// Fork: mainnet block 23032567, as in IdleCreditVaultWriteOffEscrow.t.sol
function testApr0DustRequestBlocksStopEpoch() external {
    // epoch N-1 ends; manager sets next-epoch APR to 0
    _stopEpochAndCheckPrices(0, 0 /* _newApr */, _expectedFundsEndEpoch());
    assertEq(IdleCreditVault(address(strategy)).unscaledApr(), 0);

    // attacker (KYC'd LP holding tranche tokens) makes a dust withdraw request in the buffer
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // epoch starts normally
    _startEpochAndCheckPrices(0);

    // honest manager raises the APR mid-epoch (documented manual-set path)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(initialProvidedApr, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());
    assertGt(IdleCreditVault(address(strategy)).unscaledApr(), 0);

    // epoch end reached: stopEpoch now reverts every time
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // retries keep reverting: apr0TotalPrincipal cannot be cleared (bucket close is unreachable),
    // isEpochRunning stays true, all withdraw requests stay disabled -> all funds frozen
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.isEpochRunning());

    // only recovery: manager must force unscaledApr back to 0, ending the epoch at 0 APR
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(0, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());
}
```

Caveat: the PoC assumes `requestWithdraw` routes to the APR0 path when `unscaledApr == 0` and instant-withdraw is disabled (default `disableInstantWithdraw = true`, confirmed in `_additionalInit`); helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices` follow the existing `IdleCreditVault.t.sol` harness.