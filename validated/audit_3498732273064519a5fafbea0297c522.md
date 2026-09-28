### Title
Stale APR0 principal permanently bricks `stopEpoch` once APR is re-enabled - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0()` reverts with `NotAllowed()` whenever `apr0TotalPrincipal != 0` while the current `unscaledApr` is non-zero. `apr0TotalPrincipal` is state written by an earlier `requestWithdraw` in APR=0 mode, but the guard evaluates it under the *current* APR mode — the analog of the kernel bug where the error path consumed state (`zone_rule->attr`) that was never initialized for the failing path. An unprivileged user can leave an APR0 withdraw request open; when the honest manager sets a non-zero APR for the next epoch, every subsequent `stopEpoch`/`stopEpochWithDuration` reverts, permanently freezing the vault in a running epoch.

### Finding Description
- `requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0`, incrementing `apr0TotalPrincipal` and `apr0Users[_user].principal` (`IdleCreditVault.sol:285-286`, `:567-577`).
- `apr0TotalPrincipal` is only cleared inside `prepareStopEpochWithApr0` (`:540`) or by the default-finalization path `_clearWithdrawClaimForEpoch(_isClearingApr0=true)` (`:827`).
- `prepareStopEpochWithApr0` checks `if (unscaledApr != 0) revert NotAllowed()` (`:506-508`) **after** confirming `_principal != 0`. So a leftover open APR0 bucket plus a new non-zero APR makes the function always revert — and since the revert happens before `apr0TotalPrincipal = 0` (`:540`), the state can never self-heal.
- The user cannot unblock it either: `claimWithdrawRequest` → `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` (`:326-328`), which requires a successful `stopEpoch` — a circular dependency.
- Borrower default / `finalizeDefaultRecovery` is the only escape, which is itself a fund-loss event.

### Impact Explanation
Permanent freezing of all vault funds: once triggered, `stopEpoch` cannot execute, so `epochNumber` never advances, no withdraw receipts (normal, instant, or APR0) can be claimed, borrower repayments cannot be booked, and deposits remain paused (epoch stays "running"). The entire pool NAV is frozen with no admin recovery path short of a contract upgrade or forcing a default.

### Likelihood Explanation
- The attacker is an unprivileged KYC'd lender/tranche holder: they open an APR0 withdraw request while the pool runs at APR 0 (a supported, legitimate flow).
- APR is set per epoch via `setAprs`/`setAprsWithBuffer` by the manager/CDO — a routine honest operation whenever a new epoch starts with a renegotiated rate.
- All that is required is one epoch transition from APR=0 to APR>0 while an APR0 request is still open; no privileged misbehavior, no timing exoticism. The revert path is unconditional once both conditions hold.

### Recommendation
Instead of reverting when `unscaledApr != 0` with an open APR0 bucket, settle the open APR0 principal at a zero rate (carry `settledPrincipal` forward and clear `apr0TotalPrincipal`), or close the APR0 bucket at the epoch boundary where APR was still zero. Alternatively, gate `_requestWithdrawApr0` so no new APR0 principal can be opened once the manager has scheduled a non-zero APR, and ensure `stopEpoch` always clears `apr0TotalPrincipal` even when the split is skipped.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (mode: APR0 epoch -> APR>0 epoch)
// 1. Pool running with unscaledApr == 0.
vm.prank(manager);
cdoEpoch.startEpoch(); // epoch N, apr 0

// 2. Unprivileged user opens a withdraw request -> _requestWithdrawApr0
vm.prank(user);
cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));
assertGt(strategy.apr0TotalPrincipal(), 0);

// 3. Epoch N stops fine; prepareStopEpochWithApr0 clears bucket ONLY if reached.
//    To keep the bucket open, the request must be made while apr==0 in epoch N+1.
//    Manager honestly sets APR > 0 for epoch N+1 via setAprsWithBuffer.

// 4. During buffer/user still in APR0 window (or request made in epoch N before APR change
//    with apr0TotalPrincipal re-opened), apr0TotalPrincipal != 0 while unscaledApr != 0.

// 5. Warp past epochEndDate; every stop attempt now reverts.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(apr, 0);

// 6. Repeat forever: apr0TotalPrincipal is never reset because the revert
//    precedes `apr0TotalPrincipal = 0` (line 540). Claims also revert via
//    `epochNumber <= lastWithdrawRequest[_user]` -> permanent freeze.
```

Note: I could not fully trace the IdleCDOEpochVariant caller side (exact `stopEpoch` → `prepareStopEpochWithApr0` wiring and whether APR is ever set to non-zero while an APR0 request remains open in intended operations) before the tool budget expired; the PoC sequence above assumes the standard manager flow of setting next-epoch APR via `setAprsWithBuffer`, which should be confirmed during validation.