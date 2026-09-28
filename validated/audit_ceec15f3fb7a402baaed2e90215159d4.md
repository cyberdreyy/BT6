### Title
A pending APR0 withdraw receipt permanently reverts `stopEpoch` once APR is raised above zero, freezing all vault funds - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
Any KYC'd lender can create a `apr0TotalPrincipal` entry with a dust `requestWithdraw` while `unscaledApr == 0`. `prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`, and it is called unconditionally inside `_stopEpoch`. Since nothing else clears `apr0TotalPrincipal` on a non-default path, one poisoned receipt turns any subsequent APR change into a permanent DoS of the epoch state machine.

### Finding Description
In `IdleCreditVault.requestWithdraw`, when `unscaledApr == 0` and the pool is not closed, the request is routed to `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `567-577`).

At the next `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` calls `_strategy.prepareStopEpochWithApr0(_interest)` (`IdleCDOEpochVariant.sol:362`). Inside `prepareStopEpochWithApr0`:

- `IdleCreditVault.sol:499-504` — fast path returns early only if `_principal == 0`.
- `IdleCreditVault.sol:506-508` — otherwise `if (unscaledApr != 0) revert NotAllowed();`
- `IdleCreditVault.sol:540` — `apr0TotalPrincipal = 0` is only cleared at the end of this same function, so a revert leaves the poisoned bucket intact forever.

`unscaledApr` can move off zero through normal operations: `setAprsWithBuffer` (lines 217-220), `setAprs` via `IdleCreditVaultManagerOrchestrator.setStrategyAprsRaw` (line 121-124), or the new-APR plumbing around `stopEpoch`. The only other place `apr0TotalPrincipal` is reduced is the post-default path `_clearWithdrawClaimForEpoch` (lines 822-827), which requires a borrower default and finalized recovery — not reachable in the normal lifecycle. There is no cancel-request or escape hatch for the APR0 receipt holder.

So: epoch N runs at APR 0 → attacker requests a 1-wei withdraw → `apr0TotalPrincipal = 1` → anyone (including the honest manager/operator) raises APR above 0 for epoch N+1 → every `stopEpoch` call reverts at line 507 before any state is committed. `epochNumber` never advances, `pendingWithdraws` are never funded, `epochEndDate` never lapses into a claimable state, and `claimWithdrawRequest` keeps reverting at the `epochNumber <= lastWithdrawRequest` gate (line 326).

### Impact Explanation
Permanent freezing of the entire vault TVL. All withdraw requests (normal, APR0, and queued) depend on `stopEpoch` completing to fund `pendingWithdraws` and bump `epochNumber`; tranche holders who did not request withdrawal cannot redeem either, because the epoch never closes. The attacker's cost is a single dust-sized withdraw request during any APR0 epoch plus waiting for a routine APR change — the loss is 100% of locked underlyings, mirroring the CVE where one malformed message permanently breaks rendering of the whole conversation.

### Likelihood Explanation
High given the prerequisites. APR0 epochs are a supported mode, requesting a withdraw is an unprivileged KYC'd-lender action, and changing the vault APR between epochs is a normal manager operation (the `newApr` parameter of `stopEpoch`/`stopEpochWithDuration` and `setStrategyAprsRaw` exist precisely for this). Even if the manager intends to keep APR at 0, the attacker can re-create a dust APR0 request each epoch at negligible cost, so any future APR increase — or even an accidental non-zero `newApr` — bricks the vault irreversibly. The revert is not gated by amount: `apr0TotalPrincipal = 1` suffices.

### Recommendation
Do not couple `stopEpoch` liveness to `unscaledApr`. In `prepareStopEpochWithApr0`, when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, settle the APR0 bucket at zero interest (or pro-rata on the last recorded rate) instead of reverting — e.g., write `apr0RateByEpoch[epochNumber] = 0`, keep principals claimable via `_settleApr0`, and clear `apr0TotalPrincipal`. Alternatively, reject APR increases while `apr0TotalPrincipal != 0` at the `setApr`/`setAprsWithBuffer` entry points so the poisoned state cannot be entered, and/or allow a requester to cancel an unsettled APR0 request.

### Proof of Concept
```solidity
// Fork test against deployed IdleCreditVault + IdleCDOEpochVariant (Foundry)
function testApr0ReceiptBricksStopEpoch() public {
    // --- epoch N running with unscaledApr == 0 ---
    vm.prank(manager);
    cdoEpoch.startEpoch(); // epoch started at APR 0

    // Attacker: KYC'd lender with a tranche position requests a dust withdraw
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // apr0TotalPrincipal = 1

    // Honest manager raises APR for next epoch (normal operation)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(IdleCreditVault.NotAllowed.selector);
    cdoEpoch.stopEpoch(5e18, 0); // reverts at prepareStopEpochWithApr0 L506-508

    // State is unrecoverable: apr0TotalPrincipal is still 1, unscaledApr != 0.
    // Every subsequent stopEpoch reverts; no user-facing path clears the bucket.
    vm.prank(manager);
    vm.expectRevert(IdleCreditVault.NotAllowed.selector);
    cdoEpoch.stopEpoch(5e18, 0);

    // Consequence: pending withdraws never funded, epochNumber frozen,
    // claimWithdrawRequest reverts -> all vault funds permanently locked.
}
```

Uncertainty note: the exact call site that writes `unscaledApr` during the normal `stopEpoch(newApr)` flow (whether the strategy's `setAprsWithBuffer` is invoked by the CDO in the same transaction or a separate manager call) was not fully traced in `IdleCDOEpochVariant`; however, `setStrategyAprsRaw`/`setApr` provide a direct operator path that produces the same poisoned state independently of epoch plumbing.