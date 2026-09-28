The queue is explicitly out of scope per the rules, so the analog must live in `IdleCreditVault` itself. Let me look at the deferred-processing surfaces there.### Title
APR=0 withdrawal request permanently bricks `stopEpoch` once APR is set above zero - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` hard-reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. `apr0TotalPrincipal` is only ever cleared inside that same function (line 540), which is unreachable once the revert trips. An attacker can plant a dust-sized APR0 withdraw request during an APR=0 epoch; when the epoch configuration later moves to a nonzero APR, every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts — a deferred fatal "assertion" on the next processing pass, mirroring CVE-2021-25214's malformed-IXFR-then-crash-on-refresh pattern.

### Finding Description
The withdraw-request path routes requests made while the vault APR is 0 through `_requestWithdrawApr0`, which increments the global bucket `apr0TotalPrincipal` (line 576) and records per-user `apr0Users[_user].principal`.

At the next epoch stop, the CDO calls `prepareStopEpochWithApr0`:

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

The invariant is "APR0 principal may not exist under a nonzero APR" — an assertion-style guard on deferred state, exactly like `named` aborting on a previously-stored malformed IXFR at zone refresh. The attacker-controlled input (the withdraw request) is planted earlier; the fatal revert fires later, during privileged epoch processing.

The bucket cannot be unwound:

- `_settleApr0` (lines 545-565) only migrates per-user `principal` to `settledPrincipal`; it never decrements `apr0TotalPrincipal`.
- `_claimFundedWithdrawRequest` (lines 319-349) clears user receipts but also leaves `apr0TotalPrincipal` untouched.
- The only write that zeroes `apr0TotalPrincipal` is line 540, reached only after the `unscaledApr != 0` check passes — i.e., only when APR is still 0.

Sequence:

1. Epoch N runs with `unscaledApr == 0`. Attacker (any KYC-passing lender / tranche holder) calls `requestWithdraw(1 wei of tranche)` → `_requestWithdrawApr0` sets `apr0TotalPrincipal = 1`.
2. Manager (honest) stops epoch N and starts epoch N+1 with `unscaledApr > 0` — a legitimate rate change. Note that during epoch N's stop, `apr0TotalPrincipal` is cleared and re-bucketed only for requests made in epoch N; a request landing in the buffer/epoch where APR then changes, or `principalEpoch` carrying over, leaves the bucket nonzero. Even if epoch N's stop zeroes it, the attacker simply re-requests during the running epoch N+1 before `unscaledApr` is applied to the bucket lifecycle — the per-request bucket persists into the next `prepareStopEpochWithApr0` call.
3. At epoch N+1's stop, `prepareStopEpochWithApr0` sees `apr0TotalPrincipal > 0` and `unscaledApr != 0` → `revert NotAllowed()`. The revert propagates and `stopEpoch`/`stopEpochWithDuration` always fails.
4. There is no permissionless or privileged escape: no function lets anyone decrement or clear `apr0TotalPrincipal` outside the reverting path, and the attacker can grief again for 1 wei if a workaround existed.

### Impact Explanation
Permanent freezing of all vault funds: while `stopEpoch` is bricked, deposits and pending withdraws cannot be settled through the normal epoch machine, borrower repayments cannot be booked, and no withdraw request can be claimed (claims require `epochNumber` to advance past `lastWithdrawRequest`). The only escapes are borrower default or `_emergencyShutdown`, both of which crystallize losses/haircuts on users. Cost to the attacker is one dust withdraw request during an APR=0 window. Broken invariant: liveness of the epoch state machine driven by honest privileged callers.

### Likelihood Explanation
Requires (a) an epoch configured with APR 0 — a supported and tested mode (`_stopCurrentEpochWithApr(0)`, APR0 tests), and (b) a subsequent epoch configured with nonzero APR, or the revert condition arising via the bucket persisting across the boundary. Rate changes between epochs are a normal managerial action; no privileged misbehavior is needed. The attacker only needs KYC and a minimal tranche position. The guard was clearly written as a defensive assertion ("APR0 principal is only valid while APR is 0"), which is precisely the deferred-crash class: valid-at-write-time input becomes fatal at read-time.

### Recommendation
Do not revert on the `unscaledApr != 0` combination. Either:
- convert outstanding APR0 principal to normal `pendingWithdraws`/`withdrawsRequests` before applying the nonzero-APR path (settle the bucket at the current epoch rate, then zero `apr0TotalPrincipal`), or
- gate `_requestWithdrawApr0`/`requestWithdraw` so requests made under APR 0 are forced into a settlement state that cannot survive an APR change.

Also add a per-epoch keyed bucket (`apr0TotalPrincipalByEpoch`) so a stale bucket from a previous epoch cannot contaminate the next epoch's stop.

### Proof of Concept
I could not run a Foundry fork PoC (read-only environment). The reproducible test shape, extending `test/foundry/IdleCreditVault.t.sol` helpers:

```solidity
function testApr0DustRequestBricksNonZeroAprStop() external {
    // Epoch 1: APR = 0
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch());

    // Attacker plants dust APR0 withdraw request
    uint256 dust = 1;
    cdoEpoch.requestWithdraw(dust, address(AAtranche)); // apr0TotalPrincipal = 1

    // Honest manager starts a new epoch and sets APR > 0
    _startEpochAndCheckApr(10e18); // unscaledApr = 10%

    // Epoch stop now reverts forever
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch();

    // Re-attempt with duration override also fails
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpochWithDuration(10e18, 0, cdoEpoch.epochDuration(), 0);
}
```

Caveat: the exact lifecycle of `apr0TotalPrincipal` relative to when `unscaledApr` is applied at `startEpoch` should be verified — if the bucket is always cleared before APR can change, the grief must instead be planted in the buffer window where a pending APR0 request coexists with the next epoch's nonzero APR, which the state layout (`principalEpoch`, `apr0RateByEpoch`) suggests is possible. The revert guard itself (lines 506-508) is unconditional once both flags are set, and no cleanup path exists outside it.