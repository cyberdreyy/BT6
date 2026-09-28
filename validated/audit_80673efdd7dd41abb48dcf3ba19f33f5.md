### Title
`stopEpoch` permanently reverts while an APR0 withdraw bucket is open and a new non-zero APR was set, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a stale-snapshot check: `Project.addTasks()` enforces `_taskCount == taskCount`, so a dispute raised against an old `taskCount` reverts once other tasks were added during the dispute-resolution delay. The closest analog in idle-tranches is the APR0 guard inside `prepareStopEpochWithApr0()`: once an unprivileged user opens an APR0 withdraw receipt (`apr0TotalPrincipal != 0`), the vault-wide `stopEpoch`/`stopEpochWithDuration` transition reverts whenever the currently configured `unscaledApr` is non-zero — a state that can legitimately drift between the request and the delayed epoch settlement, exactly like the stale `taskCount`.

### Finding Description
`IdleCreditVault.requestWithdraw()` routes requests into the APR0 bucket when `unscaledApr == 0` at request time, increasing `apr0TotalPrincipal` (`_requestWithdrawApr0`, `IdleCreditVault.sol:567-577`). That bucket is only closed inside `prepareStopEpochWithApr0()`, which is called from `IdleCDOEpochVariant.stopEpochWithDuration()` before any borrower funds are pulled (`IdleCDOEpochVariant.sol:362`). It contains the stale-consistency check:

```
// IdleCreditVault.sol:505-508
if (unscaledApr != 0) {
    revert NotAllowed();
}
```

`unscaledApr` is a live, mutable value: the manager can call `setAprs()` mid-epoch, and `_setScaledApr(_newApr)` at the end of a `stopEpoch` (`IdleCDOEpochVariant.sol:471`) arms a non-zero APR for the *next* epoch while APR0 receipts requested in the previous zero-APR window can still be pending (APR0 claims settle lazily via `_settleApr0`, and `apr0TotalPrincipal` is only reset by a successful `prepareStopEpochWithApr0`).

Concrete sequence:
1. Epoch N runs with `unscaledApr = 0`. Any KYC'd lender calls `requestWithdraw()` → `apr0TotalPrincipal > 0` (`IdleCreditVault.sol:285-286`).
2. Before or during the buffer, the manager (honest) sets a non-zero APR for the upcoming epoch via `setAprs()` or arms it as `_newApr` on the prior `stopEpoch`.
3. `stopEpoch` for epoch N reverts at `IdleCreditVault.sol:506-508` because `apr0TotalPrincipal != 0 && unscaledApr != 0`.

Every subsequent `stopEpoch` call reverts identically as long as the APR stays non-zero, and there is no path for the APR0 receipt holder to clear `apr0TotalPrincipal` unilaterally — settlement requires a completed stop. This mirrors the report: a value snapshot (APR at request time) is compared against live state at resolution time, and the delayed privileged action reverts.

### Impact Explanation
Temporary freezing of all vault funds with real loss surface. While `stopEpoch` reverts, the epoch cannot close: borrower repayment/interest cannot be pulled, `pendingWithdraws` cannot be funded, and every pending withdraw receipt (not just the APR0 one) is stuck. The freeze persists until the manager restores `unscaledApr = 0` and retries. If the freeze spans a period where the borrower position deteriorates or a default would otherwise have been processed, the delayed settlement crystallizes a worse outcome for all LPs. Loss magnitude is proportional to pool TVL for the freeze duration; the triggering action (an APR0 `requestWithdraw`) is available to any KYC-passing tranche holder with no capital cost beyond the deposit itself.

### Likelihood Explanation
Requires only (a) an epoch running at APR 0 — a supported mode with dedicated tests — and (b) an APR change while an APR0 receipt is open, which is a routine manager action (APR is re-armed on every `stopEpoch` via `_newApr`). Notably, `test/foundry/IdleCreditVault.t.sol:3331-3337` already encodes the revert (`setAprs(1e18,1e18)` then `stopEpoch` reverts `NotAllowed`), confirming the revert path is reachable in production code, not just theoretical. If this revert is an acknowledged design guard, the residual issue is that it is triggerable by an unprivileged user against the *global* epoch transition rather than being scoped to the APR0 claim itself.

### Recommendation
Decouple the epoch transition from the stale check, analogous to skipping `taskCount` for the disputes caller:
- Either reject `setAprs()`/`_setScaledApr` to non-zero while `apr0TotalPrincipal != 0` (fail the cheap, recoverable config call instead of the epoch settlement), or
- Inside `prepareStopEpochWithApr0`, when `unscaledApr != 0`, settle the open bucket at zero APR0 interest (`apr0RateByEpoch[epochNumber] = 0`, `apr0TotalPrincipal = 0`) instead of reverting, so the epoch always closes and APR0 claims degrade gracefully to principal-only.

### Proof of Concept
```solidity
// Foundry fork PoC (mainnet fork, against deployed IdleCDOEpochVariant + IdleCreditVault)
function testApr0BucketBlocksStopEpoch() public {
    // 1. Epoch running with unscaledApr == 0
    vm.prank(manager);
    IdleCreditVault(strategy).setAprs(0, 0);
    _startEpoch(); // epoch N running

    // 2. Unprivileged KYC'd user requests withdraw -> APR0 bucket opens
    vm.prank(user); // holds AA tranches
    cdoEpoch.requestWithdraw(amount, address(aaTranche));
    assertGt(IdleCreditVault(strategy).apr0TotalPrincipal(), 0);

    // 3. Manager sets non-zero APR for next epoch (routine action)
    vm.prank(manager);
    IdleCreditVault(strategy).setAprs(5e18, 5e18);

    // 4. Epoch end reached: stopEpoch reverts -> epoch cannot close, funds frozen
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, expectedInterest);

    // pending withdraws unfunded; all subsequent retries revert until APR reset to 0
    assertGt(IdleCreditVault(strategy).pendingWithdraws(), 0);
}
```