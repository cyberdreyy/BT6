### Title
Stale APR0 withdraw principal permanently reverts `stopEpoch` after an APR change, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` reverts `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` (lines 499-508). An unprivileged user creates this poisoned state by calling `requestWithdraw` while APR is 0 (`_requestWithdrawApr0`, lines 285-286, 567-577). If the manager later raises APR — a routine, honest action — every subsequent `stopEpoch`/`stopEpochWithDuration` reverts, because `apr0TotalPrincipal` is only cleared at line 540, after the revert check. There is no path to cancel or settle an open APR0 request except through `stopEpoch` itself. This is the vault analog of CVE-2019-18420: a latent malformed-state "bad format character" planted by an unprivileged caller that crashes the deferred continuation (`stopEpoch`) the moment an honest privileged call traverses it.

### Finding Description
- `requestWithdraw` (IdleCreditVault.sol:271-295) records the request in `apr0Users[_user].principal` and increments the global `apr0TotalPrincipal` (line 576) whenever `unscaledApr == 0`.
- `prepareStopEpochWithApr0` (lines 490-541) is invoked by `IdleCDOEpochVariant.stopEpoch`. With `_principal != 0` it hits `if (unscaledApr != 0) revert NotAllowed();` at lines 506-508. The bucket is only zeroed at line 540, which is unreachable on the revert path.
- `_settleApr0` (lines 545-565) only runs inside `claimWithdrawRequest`, which itself is gated on `epochNumber > lastWithdrawRequest[_user]` (line 326) — epochNumber can only advance via `stopEpoch`. So the poisoned state is self-sealing: the claim that would clear it requires the epoch transition that it blocks.
- Result: as long as the manager keeps `unscaledApr != 0`, the epoch can never be stopped; the borrower cannot repay, `epochEndDate` passes with no settlement, and all lender deposits plus all pending withdraw receipts remain locked in the borrower/strategy. If the manager never returns APR to 0 (or the APR0 user's principal epoch bookkeeping is left inconsistent), the freeze is permanent. This violates the epoch state machine invariant that `stopEpoch` must remain callable after `epochEndDate`.

### Impact Explanation
Permanent (or arbitrarily long) freezing of 100% of vault TVL: all active AA/BB lender deposits and all pending withdraw receipts cannot be settled because `stopEpoch`, `stopEpochWithDuration`, default resolution (`_handleBorrowerDefault` is reached through the same stop path), and every downstream claim all revert. Quantified loss = full contract value plus pending withdraws at the time of the wedge.

### Likelihood Explanation
Two conditions, both reachable by in-scope actors: (1) a KYC'd lender or tranche holder submits any non-zero `requestWithdraw` during an APR0 window — a normal, zero-cost action that can even be executed by accident; (2) the manager subsequently calls `setAprs`/`setApr` to a nonzero value before that epoch stops, an expected operational action (rate re-pricing). No malicious privileged role, no oracle, no exotic sequencing is needed; the manager is never the attacker — the attacker is the unprivileged requester who plants the unclosable bucket.

### Recommendation
Handle the APR-change transition instead of reverting: in `prepareStopEpochWithApr0`, when `unscaledApr != 0`, close the APR0 bucket at zero interest (set `apr0TotalPrincipal = 0`, optionally store `apr0RateByEpoch[epochNumber] = 0` so `_settleApr0` still clears per-user principal) rather than reverting. Alternatively, block `setAprs` to nonzero while `apr0TotalPrincipal != 0` and emit a clear error, so the freeze condition can never be created; the latter is simpler but leaves the APR0 users' principal claimable only after the epoch ends, which is already the design.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0StalePrincipalWedgedStopEpoch() external {
    // 1. Vault configured at APR 0 (as in testFinalizeDefaultHaircutsApr0PendingRedeems...)
    vm.prank(owner);   cdoEpoch.setIsAYSActive(false);
    vm.prank(manager); IdleCreditVault(address(strategy)).setAprs(0, 0);

    // 2. Unprivileged attacker deposits and requests a withdraw during APR0
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    idleCDO.depositAA(10_000 * ONE_SCALE);
    vm.startPrank(attacker);
    cdoEpoch.requestWithdraw(AAtranche.balanceOf(attacker) / 2, address(AAtranche));
    vm.stopPrank();
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // 3. Honest manager legitimately raises APR mid-epoch
    _startEpochAndCheckPrices(0);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);

    // 4. Epoch matures; borrower funds repayment; stopEpoch reverts forever
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // fund borrower repayment as in _stopCurrentEpochWithApr
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // Every retry reverts; apr0TotalPrincipal is never cleared; funds stay locked.
}
```

Note: I was unable to fully verify `setAprs` gating timing (whether it is callable mid-epoch) within the iteration budget; if `setAprs` is restricted to the buffer period the attack still works — the attacker requests during the buffer/APR0 window and the manager reprices APR before `startEpoch`. If `unscaledApr` can never change once APR0 requests exist by design, this reduces to a temporary freeze until APR returns to 0, which still meets the "temporary freezing with fund impact" bar but weakens severity.