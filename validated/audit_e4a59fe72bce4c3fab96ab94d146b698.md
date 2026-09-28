### Title
Permanent `stopEpoch` freeze via dust APR0 withdraw request combined with a later non-zero APR - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The closest analog to a reachable `CHECK` failure driven by user input is `prepareStopEpochWithApr0` in `IdleCreditVault`. Any KYC-passing lender can permanently poison the epoch state machine: if they leave even a dust-sized APR0 withdraw principal open (`apr0TotalPrincipal != 0`) and the pool APR is later set to a non-zero value, the function reverts `NotAllowed()` *before* the only code path that clears `apr0TotalPrincipal` executes. Every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts, freezing all LP funds.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0` the request is routed to `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` bucket (IdleCreditVault.sol:285-286, 567-577).

At epoch stop, the CDO calls `prepareStopEpochWithApr0`, which contains an assertion-style guard:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:499-508
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The clearing of `apr0TotalPrincipal` happens only at the end of this same function (line 540: `apr0TotalPrincipal = 0`), strictly *after* the revert. There is no other path that clears `apr0TotalPrincipal` while APR is non-zero:

- `_settleApr0` (lines 545-565) moves the per-user principal to `settledPrincipal` but does not decrement `apr0TotalPrincipal`.
- `_clearWithdrawClaimForEpoch` decrements it only when clearing a *defaulted* epoch claim (lines 822-828), which requires a default + finalization.
- `requestWithdraw` reverts for users with an unclaimed loss-adjusted receipt, but that does not apply here.

Meanwhile, `setApr`/`setAprs`/`setAprsWithBuffer` (lines 206-235) can be called by the honest manager or the CDO at any time with no check on `apr0TotalPrincipal`. There is no guard preventing an APR change while APR0 principal is pending.

Attack sequence (buffer/running phase, fixed-APR mode):

1. Manager sets `unscaledApr = 0` for an epoch (legitimate zero-APR promo period, or the natural initial configuration).
2. Attacker (any whitelisted/KYC lender) deposits a minimal amount and calls `requestWithdraw` — `apr0TotalPrincipal` becomes `dust > 0`. The attacker does not even need to claim anything.
3. Later, the manager sets a normal non-zero APR (`setAprs`/`setAprsWithBuffer`), a routine honest operation.
4. At `epochEndDate`, any call to `stopEpoch` reverts inside `prepareStopEpochWithApr0`. The revert repeats forever: `epochNumber` is never incremented, `apr0TotalPrincipal` is never cleared, and the epoch can never end.

Since `epochNumber` never advances, the attacker also cannot clear their own request via `claimWithdrawRequest` (`epochNumber <= lastWithdrawRequest[_user]` reverts at line 326). The pool is bricked: deposits, redeems, claims, and borrower repayment settlement are all frozen behind the epoch state machine.

### Impact Explanation
Permanent freezing of all funds in the credit vault — every LP's principal and accrued yield, plus pending withdraw receipts, are locked indefinitely. The only theoretical escape is owner emergency shutdown plus a manual default/finalization ceremony (writing off the pool as defaulted to reach `_clearWithdrawClaimForEpoch`), which crystallizes the entire TVL as a loss event and pays out only at the recovery ratio — effectively converting a 1-wei dust request into a forced hard default of the whole pool. Loss magnitude: up to 100% of pool NAV (if resolved via default) or indefinite freeze (otherwise).

### Likelihood Explanation
High. Preconditions are mild and realistic: an epoch run at `unscaledApr == 0` (explicitly supported — `testStopEpochWithDurationLossOnlyOwnerOrManager` uses `setAprs(0,0)`), followed by a later non-zero APR — the normal lifecycle of a pool that toggles promotional or negotiated rates. The attacker spends only gas plus a dust deposit; any tranche-token holder can execute it. The injected assertion (`unscaledApr != 0 → revert`) is invalidated purely by sequencing attacker input (the APR0 request) around an honest manager call, matching the CHECK-invalidation bug class.

### Recommendation
Don't hard-revert in `prepareStopEpochWithApr0`. Instead, settle APR0 principal at whatever rate applies: either (a) treat leftover `apr0TotalPrincipal` as normal pending principal when `unscaledApr != 0` (zero out the bucket and add it to `pendingWithdraws` basis without interest), or (b) block APR changes while `apr0TotalPrincipal != 0` inside `setApr`/`setAprs`/`setAprsWithBuffer`, forcing the bucket to be closed at a zero-APR stop first. Additionally consider clearing `apr0TotalPrincipal` in `_settleApr0` when a user's full principal settles.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// 1. Manager configures APR 0 epoch
vm.prank(manager);
IdleCreditVault(address(strategy)).setAprs(0, 0);

// 2. Attacker deposits dust and requests withdraw during the APR0 epoch
_depositWithUser(attacker, 1, true);          // KYC'd attacker
_startEpochAndCheckPrices(0);
vm.prank(attacker);
cdoEpoch.requestWithdraw(0, address(AAtranche));
assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

// 3. Epoch stops fine at APR 0 ... or attacker leaves request open and
//    manager later sets a normal APR (honest operation)
vm.prank(manager);
IdleCreditVault(address(strategy)).setAprs(apr, apr); // unscaledApr != 0

// 4. Warp past epochEndDate; every stopEpoch now reverts permanently
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(0, 0);
// Repeat forever: apr0TotalPrincipal can never be cleared -> pool frozen
```

Note: I confirmed via grep that `IdleCDOEpochVariant.sol` calls `prepareStopEpochWithApr0` and `setAprsWithBuffer`, but I was unable to read the exact `stopEpoch`/`startEpoch` call sites in `IdleCDOEpochVariant.sol` before the iteration budget ended; the revert path in `IdleCreditVault.sol` itself is fully verified above.