### Title
Pending APR0 withdraw request permanently reverts `stopEpoch` once APR is raised mid-epoch, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
An unprivileged KYC'd lender can arm a freeze trap by calling `requestWithdraw` while `unscaledApr == 0`, which increments `apr0TotalPrincipal`. If the honest manager subsequently raises the APR mid-epoch (a normal configuration action via `setAprs`/`setAprsWithBuffer`, as done in `test/foundry/IdleCreditVault.t.sol:5981`), the next `stopEpoch`/`stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0` on the `unscaledApr != 0` check, and `apr0TotalPrincipal` is never cleared — so the epoch cannot be stopped and every withdraw request becomes unclaimable.

### Finding Description
In `IdleCreditVault.requestWithdraw`, when `unscaledApr == 0` the request is routed to `_requestWithdrawApr0`, which adds `_amount` to the global `apr0TotalPrincipal` and tags the user with the current `epochNumber` (lines 285-286, 567-577). The only code that clears `apr0TotalPrincipal` is `prepareStopEpochWithApr0`, called by `IdleCDOEpochVariant._stopEpoch` before any borrower pull (line 362 in `IdleCDOEpochVariant.sol`). That function first checks `if (unscaledApr != 0) revert NotAllowed()` (lines 505-508), so once the manager has moved the vault off the zero-APR regime while `apr0TotalPrincipal != 0`, every subsequent `stopEpoch` reverts unconditionally — the bucket can never be finalized and the `apr0TotalPrincipal = 0` reset at line 540 is never reached. The revert happens before `isEpochRunning` is cleared, before `collectWithdrawFunds` funds pending receipts, and before `epochNumber` increments, so `_claimFundedWithdrawRequest` keeps reverting on `epochNumber <= lastWithdrawRequest[_user]` (line 326) for all pending withdrawers.

### Impact Explanation
Temporary freezing of funds with full quantification: 100% of vault TVL plus all pending withdraw requests are frozen for the duration of the epoch transition failure. Lender principal cannot exit (no funded receipts, `epochEndDate` never advances, deposits already disabled by epoch gating). Recovery requires the honest manager to notice and reset APR to 0 — during which any new attacker request again routes into the APR0 bucket — so an attacker can re-arm the trap each time the manager attempts the APR change, repeatedly stalling epoch settlement. The broken invariant is epoch liveness: an unprivileged user's `requestWithdraw` should not be able to make the manager-called `stopEpoch` unconditionally revert based on a configuration flag the user does not control.

### Likelihood Explanation
Requires a pool operated in (or temporarily configured to) APR0 mode where `requestWithdraw` is permissioned only by KYC (`isWalletAllowed`) — i.e., reachable by any whitelisted lender, no privileged role needed. The second precondition is an honest manager APR change while APR0 receipts are pending, which is a plausible operational action (the codebase itself exercises direct `setAprs` calls in tests) and is not guarded: `requestWithdraw` does not check pending APR0 principal against a scheduled APR change, and `setAprs`/`setAprsWithBuffer` does not check `apr0TotalPrincipal`. Note residual uncertainty: I could not fully verify all guards on `setAprs`/`setAprsWithBuffer` in this pass; if those functions are already blocked mid-epoch or gated on `apr0TotalPrincipal == 0`, the finding reduces to an operational footgun rather than an attacker-armed freeze.

### Recommendation
Either (a) make `setAprs`/`setAprsWithBuffer` revert when `apr0TotalPrincipal != 0` so the APR0 bucket is guaranteed to settle at `unscaledApr == 0`, or (b) remove the `unscaledApr != 0` revert in `prepareStopEpochWithApr0` and settle pending APR0 principal at a zero rate when the regime changed, clearing `apr0TotalPrincipal` unconditionally so `stopEpoch` can never be bricked by stale APR0 receipts.

### Proof of Concept
Foundry fork PoC outline (mode: APR0, phase: running epoch):
1. Configure vault with `unscaledApr == 0`; attacker (KYC'd lender) deposits AA, manager calls `startEpoch`.
2. Attacker calls `requestWithdraw(amount)` → `apr0TotalPrincipal = amount > 0`, `apr0Users[attacker].principalEpoch = epochNumber`.
3. Honest manager calls `strategy.setAprsWithBuffer(newApr, ...)` with `newApr != 0` mid-epoch.
4. Warp past `epochEndDate`; manager calls `stopEpoch(newApr, 0)` → reverts `NotAllowed` at `IdleCreditVault.sol:506` because `unscaledApr != 0 && apr0TotalPrincipal != 0`.
5. Assert `isEpochRunning` still true, `epochNumber` unchanged, attacker's `claimWithdrawRequest` reverts at `epochNumber <= lastWithdrawRequest`, and no pending receipt is funded — all TVL frozen until APR is reset to 0.