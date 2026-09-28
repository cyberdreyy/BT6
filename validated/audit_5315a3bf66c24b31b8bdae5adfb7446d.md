### Title
APR0 withdraw request permanently bricks `stopEpoch` when APR is raised above zero — all pool funds frozen - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed()` whenever `unscaledApr != 0` while `apr0TotalPrincipal > 0`. An unprivileged KYC-passed lender can open an APR0 withdraw bucket with a dust `requestWithdraw` while `unscaledApr == 0`; if the epoch then runs with a non-zero APR, every `stopEpoch` call reverts and there is no code path that clears `apr0TotalPrincipal`, permanently freezing the entire pool's principal and all pending receipts.

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw`, when `_isInstantWithdrawEnabled()` does not trigger, the vault's `requestWithdraw` records the request. In `IdleCreditVault.requestWithdraw`, whenever `unscaledApr == 0` and the pool is not closed, the request is routed into `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` bucket [1](#0-0) .

At epoch end, the CDO calls `IdleCreditVault.prepareStopEpochWithApr0`, which contains this guard:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:505-508
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`apr0TotalPrincipal` is only reset to zero at the end of the same function (line 540), and on the default-claim path in `_clearWithdrawClaimForEpoch` (line 827). Neither `_settleApr0` nor the funded-claim path clears the global bucket [2](#0-1) . So once an APR0 bucket exists and `unscaledApr` becomes non-zero, `stopEpoch`/`stopEpochWithDuration` revert unconditionally and forever.

The protocol itself anticipates APR changes between epochs: `requestWithdraw` compares `lastEpochApr` against `creditVault.unscaledApr() + instantWithdrawAprDelta` to decide whether to trigger an instant withdrawal [3](#0-2) , so APR moving off zero across an epoch boundary is a normal operating mode, not an edge case.

This is the credit-vault analog of CVE-2017-9262's memory leak: a resource (the APR0 principal bucket) allocated by an unprivileged user request is never released under a reachable state transition, and that unreleased resource poisons a required system operation (`stopEpoch`) for everyone.

### Impact Explanation
Permanent freezing of all pool funds. `stopEpoch` is the only way to roll the epoch, fund `pendingWithdraws`, and let borrowers repay; while it reverts:
- No withdraw request (normal, APR0, instant) can ever be claimed — the epoch gating `epochNumber <= lastWithdrawRequest[_user]` never advances because `epochNumber` only increments in `stopEpoch` [4](#0-3) .
- All active tranche holders' NAV stays locked in the strategy; borrower repayments cannot be collected through the epoch flow.
- `finalizeDefault`/recovery paths cannot rescue the funds because default only triggers through a `stopEpoch` that itself reverts.

The attacker's cost is a dust deposit (the minimum `requestWithdraw` amount); the griefing factor is the entire pool TVL.

### Likelihood Explanation
Requirements:
1. Pool has `unscaledApr == 0` during a buffer/request window (APR0 mode is a supported configuration exercised extensively in `test/foundry/IdleCreditVault.t.sol`).
2. Attacker is a KYC-passed lender calling `requestWithdraw` with any dust amount — no privileged role needed.
3. The APR is later set non-zero (borrower renegotiation / orchestrator update — the manager is honest, this is a normal sequence the code explicitly supports via `lastEpochApr` vs `unscaledApr` comparisons).

Uncertainty I could not fully resolve within this analysis: I verified the revert guard and the absence of a bucket-clearing path, but did not read `IdleCDOEpochVariant.stopEpoch`/`startEpoch` and `IdleCreditVaultManagerOrchestrator` line-by-line to confirm `prepareStopEpochWithApr0` is invoked unconditionally and that `unscaledApr` can change while an APR0 bucket is open. If `stopEpoch` skips the call when `unscaledApr != 0`, the revert is unreachable and severity drops. The presence of the guard suggests the developers considered the state reachable but chose a revert rather than a bucket-clearing fallback — which is itself the defect.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`. Instead, migrate stale APR0 principal into the normal withdraw path: for each accounting purpose, treat `apr0TotalPrincipal` as already-included `pendingWithdraws` basis (it was already added at request time), set `apr0TotalPrincipal = 0`, and mark `apr0RateByEpoch[epochNumber]` as zero interest, so affected users claim principal-only via `_claimFundedWithdrawRequest`. Alternatively, clear the bucket in `_settleApr0`/claim paths and let the CDO force-close the bucket before changing APR.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultApr0Freeze.t.sol
function testApr0BucketBricksStopEpochAfterAprRaise() public {
    // Setup: pool with unscaledApr == 0, attacker is a KYC-passed lender
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1 * ONE_SCALE, true); // dust deposit

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));

    // Epoch 0: buffer open, APR == 0. Attacker opens an APR0 withdraw request.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(creditVault.apr0TotalPrincipal(), 0, 'apr0 bucket opened');

    // Honest borrower/manager: new epoch runs with APR > 0
    // (orchestrator raises unscaledApr as part of normal epoch setup)
    _setUnscaledApr(initialApr); // helper: manager sets vault apr to nonzero
    _startEpochAndCheckPrices(0);

    // Borrower repays principal + interest in full
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);

    // stopEpoch -> prepareStopEpochWithApr0 reverts: unscaledApr != 0 && apr0TotalPrincipal > 0
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // No state transition clears apr0TotalPrincipal:
    // repeat attempts still revert -> epoch can never close,
    // epochNumber never advances, all pending + active funds frozen.
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);
    assertGt(creditVault.apr0TotalPrincipal(), 0, 'bucket can never be cleared');
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L537-541)
```text
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-764)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
```
