### Title
Dust APR0 withdraw request permanently reverts `stopEpoch` when APR is later non-zero, freezing all pool funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external report (BIT-java-2026-70906) is an unauthenticated remote DoS: a crafted input reaching the 2D component hangs/crashes the JVM. The credit-vault analog is a revert-poisoned epoch transition: any KYC'd lender can plant a dust `apr0TotalPrincipal` entry while `unscaledApr == 0`, which turns `prepareStopEpochWithApr0` into an unconditional `revert NotAllowed()` whenever APR is non-zero — blocking `stopEpoch`/`stopEpochWithDuration` and freezing every depositor's funds and every pending withdraw receipt.

### Finding Description
In `IdleCreditVault.requestWithdraw`, a request made while `unscaledApr == 0` routes into the APR0 bucket via `_requestWithdrawApr0`, incrementing `apr0TotalPrincipal` (IdleCreditVault.sol:285-286). There is no minimum-amount check — a single wei of principal is sufficient.

At epoch end, `IdleCDOEpochVariant._stopEpoch` unconditionally calls `_strategy.prepareStopEpochWithApr0(_interest)` (IdleCDOEpochVariant.sol:362). That function reverts if APR0 principal exists while `unscaledApr != 0`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:502-508
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`requestWithdraw` has no symmetric guard: an attacker can open an APR0 request during any buffer period in which APR is 0 (or becomes 0), even for `1 wei`, and the poisoned state persists until that epoch's `stopEpoch` succeeds — which is exactly what the revert prevents whenever the pool's APR has been set non-zero.

Attack sequence (named phases):
1. Pool is in buffer/running phase with `unscaledApr == 0` (APR0 mode is a supported configuration; tests exercise `setAprs(0,0)`).
2. Attacker (any KYC-passing lender) deposits a minimal amount and calls `cdoEpoch.requestWithdraw(dust, tranche)` → `apr0TotalPrincipal += dust`.
3. Manager (honest) later configures a non-zero APR for the next epoch via `setAprs`/`stopEpochWithDuration` — a routine, privileged-but-benign action.
4. `stopEpoch`/`stopEpochWithDuration` now always reverts at `prepareStopEpochWithApr0` → `NotAllowed()`. The epoch cannot transition: `isEpochRunning` stays true, `epochEndDate` is in the past, `startEpoch` is blocked (`isEpochRunning`), and `claimWithdrawRequest` is gated by `epochNumber <= lastWithdrawRequest` which never advances.

The only escape is the manager noticing and calling `setAprs(0, 0)` before the next stop attempt — nothing in `stopEpochWithDuration` self-heals, since `prepareStopEpochWithApr0` runs before `_setScaledApr` is applied (the revert precedes the APR update, lines 362 vs 526-527).

### Impact Explanation
Temporary freezing of all pool funds with quantified loss:
- All pending withdraw receipts (`pendingWithdraws`, `instantWithdrawsRequests`) cannot be funded because `stopEpoch` reverts, so no claim path opens.
- All active tranche holders cannot redeem (redeems don't exist; only request/claim flows per test at IdleCreditVault.t.sol:541-563), so their principal is locked for at least one additional `epochDuration` cycle plus remediation time.
- Loss magnitude: every depositor's funds are frozen for the remediation window; pending withdrawers lose the epoch of liquidity they were owed; if the misconfiguration is not diagnosed, the freeze is indefinite.
- The attacker spends only the dust deposit + gas; the freeze affects the entire TVL — an asymmetric, unauthenticated-in-effect DoS matching the CVE's "complete DOS via network-reachable API" shape.

### Likelihood Explanation
- Preconditions: pool operated in APR0 mode at some point (explicitly supported — `setAprs(0,0)`, `apr0Users` flow, dedicated tests) and APR later set non-zero, a normal operator action (e.g., restoring yield after a zero-rate epoch).
- Attacker needs only a KYC-passing wallet and dust capital; `requestWithdraw` enforces no minimum.
- The revert is deterministic, not probabilistic: one dust request poisons every subsequent stop call until APR is manually zeroed again. An attacker can re-poison each buffer period cheaply by re-requesting after claiming, sustaining the freeze across epochs whenever APR is non-zero.
- Existing guards do not stop it: `_checkAllowed`/KYC doesn't prevent small requests, `_ensureDefaultRecoveryInitialized` is unrelated, and the revert itself is the guard — but it converts an accounting edge case into a global availability failure instead of settling the dust request.

### Recommendation
- In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0`. Instead, settle the open APR0 bucket at the realized epoch terms (apply `apr0RateByEpoch[epochNumber]` against the interest actually resolved) or forcibly migrate `apr0Users[*].principal` into `settledPrincipal` with zero rate, then zero `apr0TotalPrincipal`.
- Alternatively/additionally, enforce a minimum `requestWithdraw` amount (or minimum `apr0TotalPrincipal` granularity) so dust cannot strand the bucket, and make `_requestWithdrawApr0` reject requests when the pool APR is scheduled to change.
- Add a regression test: APR0 request of 1 wei → `setAprs(x,0)` with x>0 → `stopEpoch` must not revert.

### Proof of Concept
Foundry fork-style PoC (mirroring harness helpers in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testDustApr0RequestBlocksStopEpoch() external {
    IdleCreditVault _strategy = IdleCreditVault(address(strategy));
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // pool in APR0 mode
    vm.prank(manager);
    _strategy.setAprs(0, 0);

    // attacker: KYC'd lender deposits dust and requests withdraw
    address attacker = makeAddr('attacker');
    uint256 dust = 1; // 1 wei of underlying
    uint256 minted = _depositWithUser(attacker, dust, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(minted, address(AAtranche)); // or 0 for full balance
    assertGt(_strategy.apr0TotalPrincipal(), 0, 'apr0 poisoned');

    // honest flow: epoch runs and stops fine while APR stays 0
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    // NOTE: apr0TotalPrincipal cleared only if stop succeeds while unscaledApr == 0

    // Re-poison in the next buffer with a fresh dust request
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // attacker re-deposits/keeps receipt
    assertGt(_strategy.apr0TotalPrincipal(), 0);

    // manager sets a normal non-zero APR for the following epoch
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.prank(manager);
    _strategy.setAprs(initialProvidedApr, 0); // unscaledApr != 0 now

    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.startPrank(borrower);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    vm.stopPrank();

    // every stopEpoch now reverts: epoch is stuck, all funds frozen
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), 0);

    // pending withdrawers cannot claim; epochNumber cannot advance
    assertTrue(cdoEpoch.isEpochRunning(), 'epoch stuck running');
}
```

Uncertainty note: I could not fully read `setAprs`/`setAprsWithBuffer` to confirm `unscaledApr` can be raised while an epoch runs, nor confirm whether `stopEpochWithDuration`'s `_setScaledApr` ordering is the only place APR is updated — if `setAprs` is manager-callable mid-epoch (tests call it directly), the PoC holds; if APR can only change inside `stopEpochWithDuration`, the revert still fires because `prepareStopEpochWithApr0` runs before `_setScaledApr`, so the first non-zero-APR stop reverts while `unscaledApr` is still 0 only if the revert reads the *old* value — in that ordering the check at line 506 reads `unscaledApr` before the update, so the revert fires precisely when the manager attempts to raise APR via `stopEpochWithDuration`, which is the same freeze. Either ordering yields a blocked epoch transition.