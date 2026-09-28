### Title
Stale APR0 withdraw bucket bricks `stopEpoch` after APR is raised, freezing the epoch and all borrower/LP funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestWithdraw` records a user's request in the APR0 bucket (`apr0Users[_user].principal`, `apr0TotalPrincipal`) whenever `unscaledApr == 0` at request time. `prepareStopEpochWithApr0`, called unconditionally at the top of `stopEpoch`, reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Like the QV `reviewRecipients` bug, two contradictory accounting states ("this epoch has APR0 requests" and "APR is now non-zero") coexist with no mechanism to drop or migrate the stale ones, so any single leftover APR0 request permanently reverts `stopEpoch` while APR is non-zero.

### Finding Description
During a buffer phase while `unscaledApr == 0`, a KYC'd lender calls `requestWithdraw` for a dust amount. `IdleCreditVault.requestWithdraw` routes to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` (`IdleCreditVault.sol:567-577`).

Later, the honest manager/owner raises the APR via `setAprs`/`setAprsWithBuffer` (`IdleCreditVault.sol:206-235`) — a normal operation when a pool transitions off a zero-APR epoch. Nothing clears or migrates `apr0TotalPrincipal` or `apr0Users` on an APR change.

At epoch end, `stopEpoch` calls `prepareStopEpochWithApr0`, which hits:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:505-508
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

and reverts before reaching the `apr0TotalPrincipal = 0` reset at line 540. `stopEpoch` is the only path that settles the epoch, funds `pendingWithdraws`, and triggers `_handleBorrowerDefault`; `_handleBorrowerDefault` is only reachable *inside* `stopEpoch`, so the default/recovery path cannot be reached either. The only escape is setting APR back to 0 for one stop cycle — but this defeats the APR change and still leaves every user's staked funds locked in the meantime.

### Impact Explanation
Temporary freezing of all pool funds: borrower repayment cannot be collected, no `pendingWithdraws` funding, no interest accrual settlement, and new epochs cannot start, for as long as APR stays non-zero. The cost to the attacker is one dust-sized withdraw request (their receipt remains claimable later, so the principal isn't even lost). Broken invariant: epoch state machine liveness — a per-user accounting remnant ("votes" from a previous APR regime) vetoes a global state transition.

### Likelihood Explanation
Requires only: (1) an epoch where `unscaledApr == 0`, (2) any user-level withdraw request during the buffer, (3) a subsequent APR increase — all benign, expected operations. The attacker's sole action is a dust `requestWithdraw` timed while APR is 0; the trigger is an honest manager call. No privileged misbehavior needed.

### Recommendation
- In `prepareStopEpochWithApr0`, instead of reverting when `unscaledApr != 0`, settle/migrate the leftover APR0 bucket (e.g., treat it as a normal withdraw receipt by moving `apr0TotalPrincipal`-backed user principals into `withdrawsRequests`/`withdrawsRequestsByEpoch` for `epochNumber`, then zero the bucket).
- Alternatively, invalidate or force-settle open APR0 requests in `setApr`/`setAprs` when `unscaledApr` transitions away from 0, analogous to "dropping stale votes on resubmission" in the QV report.
- At minimum, allow the claim path (`_settleApr0` / `_claimFundedWithdrawRequest`) to release APR0 principal even when no `apr0RateByEpoch` entry exists, so the bucket can be drained without `stopEpoch`.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0StaleRequestBricksStopEpoch() external {
    // 1. Configure pool with unscaledApr == 0 (APR0 mode)
    vm.prank(cdoEpoch.owner());
    strategy.setAprs(0, 0);

    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, 0, expectedInterest); // epoch 0 ends, APR still 0

    // 2. During buffer, attacker (KYC'd LP) requests a dust withdraw
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, 1 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // routed to _requestWithdrawApr0
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // 3. Manager legitimately raises APR for the next epoch
    vm.prank(manager);
    strategy.setAprs(newApr, newAprScaled);

    // 4. Epoch runs; at end, borrower funds repayment as usual
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 5. stopEpoch reverts forever while unscaledApr != 0 && apr0TotalPrincipal != 0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(newApr, 0);

    // Borrower repayment unfunded, no default path reachable, all LP funds frozen
    // until APR is restored to 0 — the stale APR0 "vote" vetoes the transition.
}
```

Uncertainty note: I verified the revert path and that `apr0TotalPrincipal` is only zeroed inside `prepareStopEpochWithApr0` after the revert check (line 540) or via default-epoch claim clearing in `_clearWithdrawClaimForEpoch`; I did not trace every admin escape hatch (e.g., whether an emergency shutdown or upgrade could recover funds without resetting APR), so the freeze may be recoverable via privileged intervention, which keeps it at "temporary freezing" severity rather than permanent loss.