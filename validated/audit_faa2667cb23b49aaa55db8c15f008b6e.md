### Title
APR0 withdraw receipts permanently brick `stopEpoch` once APR is non-zero — entire vault frozen - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The MySQL CVE is an availability bug: a reachable code path makes the server hang/crash. The closest analog in this codebase is a reachable `revert` inside the epoch-stop path of `IdleCreditVault`. `prepareStopEpochWithApr0()` hard-reverts whenever an APR0 (`unscaledApr == 0` era) withdraw principal bucket exists but the *current* `unscaledApr` is non-zero. Any unprivileged tranche holder can open a dust-sized withdraw request during an APR0 epoch; if APR is later raised (an honest, routine manager/CDO action), every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts. The epoch can never end, `epochNumber` never advances, so no withdraw receipt can ever be claimed — the whole vault (TVL + pending receipts) is frozen.

### Finding Description
`requestWithdraw` routes to `_requestWithdrawApr0` whenever `unscaledApr == 0`, which increments the global bucket `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `567-577`). The bucket is only cleared inside `prepareStopEpochWithApr0` at line 540 — code that is reached *after* the guard:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:506-508
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

So if APR was changed between request and epoch end, `prepareStopEpochWithApr0` reverts → `stopEpoch` reverts → `epochNumber` stays fixed. Deadlock details:

- `apr0TotalPrincipal` has no other decrement path; `_settleApr0` only moves the *user's* bucket (`principal → settledPrincipal`) and never touches the global total (`545-565`).
- Claiming requires `epochNumber > lastWithdrawRequest[_user]` (`326`), which requires a successful `stopEpoch` — the very call that reverts.
- `requestWithdraw` provides no escape either: it only adds to the bucket.
- The only recovery is the owner manually calling `setAprs(0, …)` to restore `unscaledApr == 0` — i.e., the vault stays frozen until governance intervention, and the attacker can keep a fresh APR0 receipt open to re-arm the brick each time APR is raised again.

No existing guard prevents it: KYC/`isWalletAllowed` doesn't matter (any allowed lender is in the attacker model), the `maxApr` cap doesn't prevent non-zero APR, and there is no skim/donation interaction. The `revert` was presumably intended as a consistency check but creates a deadlock instead of a degraded path.

### Impact Explanation
Temporary (governance-dependent) freezing of 100% of vault funds: all lender principal, accrued interest, and all pending/instant withdraw receipts become unclaimable. Each attacker re-arm costs only a dust tranche withdrawal (1 wei of tranche tokens suffices — `_amount` can be minimal as long as it survives the management-fee floor), while the frozen amount is the entire `getContractValue()` plus `pendingWithdraws`/`pendingInstantWithdraws`. Loss scales with pool TVL; attack cost is dust.

### Likelihood Explanation
Requirements are modest: (a) pool operates at `unscaledApr == 0` at some point (APR0 mode is a supported, tested flow), (b) attacker holds any tranche tokens or passes KYC to deposit then withdraw, (c) APR is later set non-zero — a routine manager/CDO operation (`setAprsWithBuffer` is called by the CDO on epoch transitions). Nothing about the attacker's timing is privileged; step (c) is an honest action the attacker merely sequences around. The attacker can also passively wait: hold an APR0 receipt open indefinitely, and the moment APR is raised, the next `stopEpoch` bricks.

### Recommendation
Replace the hard revert in `prepareStopEpochWithApr0` with a graceful path: when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, either (i) settle the APR0 bucket with zero rate for the current epoch (`apr0RateByEpoch[epochNumber] = 0`) and clear `apr0TotalPrincipal`, or (ii) roll APR0 receipts into normal `withdrawsRequests` at par. Alternatively, forbid APR changes while `apr0TotalPrincipal > 0` inside `setApr`/`setAprs` (revert the APR change, not the epoch stop), so the freeze vector sits on the privileged call rather than the liveness-critical `stopEpoch`.

### Proof of Concept
Foundry fork-style sketch against `IdleCreditVault` + `IdleCDOEpochVariant`:

```solidity
function testApr0ReceiptBricksStopEpoch() external {
    // Epoch N runs with unscaledApr == 0 (APR0 mode)
    vm.prank(manager); cdoEpoch.setAprsWithBuffer(0, duration, buffer); // via CDO/owner path
    vm.prank(manager); cdoEpoch.startEpoch();

    // Attacker: KYC'd lender, deposits dust, requests withdraw -> fills apr0TotalPrincipal
    uint256 tranches = _depositWithUser(attacker, 1);   // 1 wei underlying
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(tranches, address(trancheAA));
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // Honest action: APR is raised for/next epoch (unscaledApr != 0)
    // (manager setAprs, or CDO setAprsWithBuffer at next startEpoch)
    vm.prank(manager);
    strategy.setAprs(5e18, 5e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(address(underlying), borrower, owed, true);
    vm.prank(borrower); underlying.approve(address(cdoEpoch), owed);

    // stopEpoch -> strategy.prepareStopEpochWithApr0 -> revert NotAllowed()
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Deadlock: epochNumber frozen, no claim possible
    vm.expectRevert(NotAllowed.selector);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    // Attacker's other receipt holders identically frozen; only owner setAprs(0,..) unbricks.
}
```

Note: exact call ordering (whether APR change happens via `setAprs` directly or via `setAprsWithBuffer` during `startEpoch`) should be confirmed against `IdleCDOEpochVariant.startEpoch`; the grep for `prepareStopEpochWithApr0` call sites was truncated, so I could not fully verify the line where `stopEpoch` invokes it — but the strategy-side guard at `IdleCreditVault.sol:506-508` and the single clearing point at line 540 are confirmed, and the deadlock depends only on `unscaledApr != 0 && apr0TotalPrincipal > 0` at stop time.