### Title
Stale APR0 withdraw bucket permanently pins `unscaledApr` to 0, blocking all future APR increases - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` buckets a withdrawal request into the APR0 accounting path (`apr0Users`, `apr0TotalPrincipal`) based on the `unscaledApr` value that was in force *at request time*. `prepareStopEpochWithApr0` then enforces that this stale classification remains valid by reverting whenever `apr0TotalPrincipal != 0` while `unscaledApr != 0`. This is the direct analog of CVE-2026-11564: a resource (the withdraw receipt / APR0 bucket) is created under one "trust configuration" (APR = 0), and the vault keeps honoring that old configuration after the manager switches the handle (the APR) to a new one — the stale state is not invalidated on the mode switch, it blocks the switch.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:285-286) a request is routed to `_requestWithdrawApr0` whenever `unscaledApr == 0` at request time, incrementing `apr0TotalPrincipal` and storing `apr0Users[user].principal`/`principalEpoch`.

In `prepareStopEpochWithApr0` (contracts/strategies/idle/IdleCreditVault.sol:499-508), called by the CDO during every `stopEpoch`, the code does:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) { return (...); }
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`apr0TotalPrincipal` is only cleared inside the same function when `unscaledApr == 0` (line 540). `_settleApr0`/`_clearWithdrawClaimForEpoch` clear per-user data but do not reduce `apr0TotalPrincipal` below the user's principal except on the default-clearing path.

Consequence: while any APR0 receipt principal is outstanding, every `stopEpoch` call made with a non-zero `unscaledApr` reverts. The manager's only way to settle the bucket is to stop the epoch with APR still equal to 0 — after which an attacker can simply open a new dust-size `requestWithdraw` during the following buffer/stopped window (requests are permissionless for any `isWalletAllowed` user while `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are true, IdleCDOEpochVariant.sol:739-744), recreating a non-zero `apr0TotalPrincipal` before the next `stopEpoch`. The vault can never transition back to a positive APR.

Guards that do not stop this:
- There is no owner/manager setter to refuse normal withdraw requests or to force-clear `apr0TotalPrincipal` without stopping an epoch at APR 0.
- `setIsDepositDuringEpochDisabled`, `disableInstantWithdraw`, and `_pause` do not reset the bucket; `restoreOperations` doesn't clear it either.
- The `lossRecoveryPriceByEpoch`/`lastWithdrawRequest` guard (lines 261-271) only blocks the *same* user from stacking requests, not a fresh attacker address each cycle.
- A1-sized (dust) principal suffices; the revert triggers on `apr0TotalPrincipal != 0` regardless of magnitude.

### Impact Explanation
An unprivileged KYC-passing lender can permanently force the pool's APR to remain 0. All interest that would accrue to AA/BB tranche holders in every subsequent epoch is zeroed: the borrower (who is honest but rationally pays 0 when the rate is 0) effectively borrows for free, and all LP yield is forfeited for as long as the attacker keeps a dust APR0 receipt open — which costs only the upfront management fee on a minimal withdrawal. This is theft of unclaimed yield / permanent freezing of yield accrual, quantified as 100% of expected epoch interest for every epoch the pin is maintained (e.g., at 10% APR on 1M TVL, ~100k per year-epoch). There is no recovery path that restores a positive APR while an APR0 bucket exists, since `stopEpoch` itself is the only bucket-clearing mechanism and it is the function being bricked for `unscaledApr != 0`.

### Likelihood Explanation
- Attacker requirements: any `isWalletAllowed` address holding tranche tokens; repeated dust `requestWithdraw` calls during windows when `unscaledApr == 0` (i.e., after any epoch stopped with `_newApr = 0`, or on APR0-configured pools trying to migrate to positive APR).
- No privileged cooperation is needed; the revert path is deterministic.
- The only cost is gas plus the upfront management fee on a minimal receipt (`_totalWithdrawFees` on dust), which can be near zero.
- Likelihood is bounded by the pool ever operating at APR 0 (common for programmable/零-interest borrower configurations being migrated, or any epoch stopped with `_newApr = 0`).

### Recommendation
Do not let a stale request-time APR classification veto the current epoch's APR. Options:
- Settle outstanding APR0 principal at the *new* epoch's realized interest (or at 0 interest) instead of reverting: in `prepareStopEpochWithApr0`, when `unscaledApr != 0`, finalize the bucket by moving `apr0TotalPrincipal` principal to a settled state with `apr0RateByEpoch[epochNumber] = 0` and clearing `apr0TotalPrincipal`, rather than reverting.
- Alternatively, timestamp-bound APR0 receipts: record the APR at request time per receipt and treat receipts as normal funded withdrawals (par claim of principal) if the APR regime changes before their settlement epoch.
- Whichever fix is chosen, add a test that requests an APR0 withdrawal, then `stopEpoch`s with a non-zero `_newApr`, and asserts the epoch settles and the receipt is claimable.

### Proof of Concept
Foundry fork PoC sketch (build on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_toggleEpoch`):

```solidity
function testApr0ReceiptPinsApr() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    // Epoch 0 runs and is stopped with next-epoch APR = 0
    _startEpochAndCheckPrices(0);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, _expectedFundsEndEpoch()); // unscaledApr == 0 now
    assertEq(IdleCreditVault(address(strategy)).unscaledApr(), 0);

    // Attacker (any allowed wallet) opens a dust APR0 withdraw request in the buffer
    address attacker = makeAddr('apr0-pinner');
    _depositWithUser(attacker, ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust -> apr0 bucket
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Manager tries to restore a positive APR at the next stopEpoch -> reverts
    _startEpochAndCheckPrices(1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(10e18, _expectedFundsEndEpoch()); // blocked: stale APR0 bucket

    // Only APR = 0 succeeds; attacker re-pins in the next buffer and the cycle repeats
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, _expectedFundsEndEpoch());
}
```

The asserted `NotAllowed` revert demonstrates that a request-time APR classification created under `unscaledApr == 0` survives the configuration switch and vetoes every subsequent positive-APR settlement, matching the stale-state-after-switch bug class.