### Title
A dust APR0 withdraw request permanently bricks `stopEpoch` once the pool APR is raised, freezing all user and borrower funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The bug class is an unconstrained carried value at a state transition: in `binary.pil` the carry `cIn` was left free when `RESET == 1`. The analog here is `apr0TotalPrincipal` in `IdleCreditVault`: it is a "carry-in" bucket that must be zero whenever the APR flag (`unscaledApr`) is non-zero, but the constraint is only enforced inside `prepareStopEpochWithApr0` as a hard revert, and nothing zeroes the bucket on the transition. An unprivileged KYC-passed lender can open a dust APR0 withdraw receipt, and any subsequent APR change by the honest manager makes every future `stopEpoch`/`stopEpochWithDuration` revert, freezing all funds in the epoch state machine.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0` and the pool is not closed, the requested amount is routed into the APR0 bucket via `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` (`IdleCreditVault.sol:567-577`, `:285-286`, `:279`). There is no minimum amount — a 1-wei request suffices.

`prepareStopEpochWithApr0` then enforces the invariant `apr0TotalPrincipal != 0 => unscaledApr == 0` by reverting (`IdleCreditVault.sol:506-508`):

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) { return ...; }
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The bucket is only cleared at the end of the same function (`:540`), i.e. only if the check passes. `setApr`/`setAprs` (`:206-235`) and `_settleApr0` (`:545-565`) never touch `apr0TotalPrincipal`, and `_claimFundedWithdrawRequest` cannot clear it either: claiming requires `epochNumber > lastWithdrawRequest[_user]` (`:326-328`), which requires a successful `stopEpoch`, which is exactly what reverts. The only other decrements of `apr0TotalPrincipal` are in the default-finalization path `_clearWithdrawClaimForEpoch` (`:822-828`).

The honest manager can set a non-zero APR mid-epoch via `setAprs` (allowed for manager at `:230`), or set it for the next epoch via `stopEpochWithDuration` → `_setScaledApr` — but note `_setScaledApr` runs *after* `_stopEpoch` (`IdleCDOEpochVariant.sol:522-527`), so the trap arms silently: the stop that *raises* the APR succeeds (bucket still under old APR... actually the bucket zeroes there), while a stop called with `unscaledApr != 0` and a *newly opened* APR0 request is impossible. The concrete exploit ordering is:

1. Pool runs an epoch with `unscaledApr == 0`. Attacker deposits AA/BB (KYC'd lender) and calls `requestWithdraw` with 1 wei during the buffer/running phase → `apr0TotalPrincipal = 1`.
2. Honest manager raises the APR mid-epoch via `IdleCreditVault.setAprs(newApr, scaledApr)` — a legitimate operation the manager is explicitly permitted to perform (`:223-230` comment: "If manager manually set apr from here...").
3. From now on every `stopEpoch`/`stopEpochWithDuration` reverts at `prepareStopEpochWithApr0` (`:507`). Funds can never be pulled from the borrower, withdraws can never be fulfilled, and the epoch is stuck running until the manager lowers `unscaledApr` back to 0 — which may itself be economically unacceptable, and the attacker can re-arm the trap with a fresh dust request in every APR0 epoch.

### Impact Explanation
Temporary freezing of all vault funds: while `unscaledApr != 0` and `apr0TotalPrincipal != 0`, the epoch state machine cannot transition, so no lender (including the attacker's competitors) can claim funded withdraws, no interest can be accrued, and borrower recall (`stopEpoch(0,1)`) is impossible. The freeze resolves only by returning APR to 0 and stopping under conditions the attacker can repeatedly recreate for the cost of a dust deposit plus gas. This breaks the epoch-gating solvency invariant (`_pendingInstant() == 0`, borrower repayment, `collectWithdrawFunds`) behind an honest-manager action the attacker arms but does not perform.

### Likelihood Explanation
Low-to-moderate. It requires (a) an epoch configured at APR 0 (a supported first-class mode, `unscaledApr == 0` branch at `:285`), and (b) the manager later raising the APR before that epoch stops — a normal operational action, not attacker-controlled. Once (b) happens the freeze is deterministic and recurring at near-zero attacker cost. No privileged collusion, oracle manipulation, or borrower misbehavior is needed; the attacker only needs to pass KYC to deposit and request a withdrawal.

### Recommendation
Constrain the carry at the transition instead of inside the state machine, mirroring the `cIn' = cOut * (1 - RESET')` fix:

- Revert in `setApr`/`setAprs`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0` and the new `unscaledApr != 0`, so the APR flag can never be raised while the APR0 bucket is open — this makes the invalid state unreachable rather than making `stopEpoch` unusable, **or**
- In `prepareStopEpochWithApr0`, settle-and-close the bucket instead of reverting: treat `unscaledApr != 0` APR0 receipts as normal receipts by moving `apr0TotalPrincipal` into `withdrawsRequests`-equivalent accounting and zeroing the bucket, so `stopEpoch` can never be bricked by stale APR0 state.

### Proof of Concept
Foundry fork sketch against `IdleCreditVault`/`IdleCDOEpochVariant` (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_stopCurrentEpochWithApr`):

```solidity
function testApr0DustBricksStopEpoch() external {
    // 1. configure an APR0 epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    idleCDO.depositAA(10_000 * ONE_SCALE);          // honest LP
    _startEpochAndCheckPrices(0);

    // 2. attacker (KYC'd lender) deposits dust and requests a dust withdraw
    vm.startPrank(attacker);
    underlying.approve(address(idleCDO), 1);
    idleCDO.depositAA(1);
    cdoEpoch.requestWithdraw(1, cdoEpoch.AATranche()); // apr0TotalPrincipal == 1
    vm.stopPrank();
    assertEq(strategy.apr0TotalPrincipal(), 1);

    // 3. honest manager raises APR mid-epoch (allowed at IdleCreditVault.sol:230)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 5e18);

    // 4. epoch ends: every stopEpoch path reverts in prepareStopEpochWithApr0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 duration = cdoEpoch.epochDuration();
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(5e18, 0, duration, 0);

    // borrower-recall variant also reverts
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);

    // 5. attacker's claim is also stuck (epochNumber never advanced)
    vm.expectRevert(NotAllowed.selector);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
}
```

All funds remain locked in the epoch loop until APR is returned to 0, and the attacker can re-open the dust receipt each APR0 epoch to perpetuate the condition.