### Title
APR=0 withdraw receipt permanently bricks `stopEpoch` once APR is raised — unprivileged lender can freeze all vault funds — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.requestWithdraw` records a user's pending withdrawal in a special "APR0" bucket (`apr0Users`, `apr0TotalPrincipal`) whenever `unscaledApr == 0` and the pool is not closed. `prepareStopEpochWithApr0`, which the epoch CDO must call during every `stopEpoch`, reverts `NotAllowed` whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`. An unprivileged KYC-passed tranche holder can therefore poison the epoch state machine: request a withdrawal during an APR=0 epoch, and as soon as the (honest) manager raises the APR — a routine operation — every subsequent `stopEpoch` call reverts and the bucket can never be cleared. This is the direct credit-vault analog of CVE-2021-32566's "crafted input → unhandled state → server DoS": a malformed lifecycle combination the code rejects rather than resolves, freezing the whole service.

### Finding Description
Relevant code in `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` routes into `_requestWithdrawApr0` whenever `unscaledApr == 0 && !isClosed`, incrementing the global `apr0TotalPrincipal` (lines 285–294, 567–577).
- `prepareStopEpochWithApr0` (lines 490–541) fast-paths only when `apr0TotalPrincipal == 0`. Otherwise it enforces `if (unscaledApr != 0) revert NotAllowed();` (lines 505–508) — before it ever reaches `apr0TotalPrincipal = 0` at line 540.
- The only writers of `apr0TotalPrincipal` are `_requestWithdrawApr0` (increment), `prepareStopEpochWithApr0` (reset to 0 — unreachable while the revert holds), and `_clearWithdrawClaimForEpoch` (decrement, reachable only via `claimWithdrawRequest`, which itself requires the epoch to advance, i.e. requires `stopEpoch`).

The result is a circular dependency: the APR0 bucket can only be settled by `stopEpoch`, but `stopEpoch` reverts while the bucket exists and `unscaledApr != 0`. The sole escape is the manager setting APR back to 0 — at which point the same attacker (or any leftover requester) can re-request a withdrawal and re-arm the trap. The check is intended as a consistency guard, but it is attacker-triggerable rather than a guard on privileged misuse: `requestWithdraw` performs no defense against mixing APR0 buckets with later APR changes, exactly the improper-input-validation pattern of the CVE.

### Impact Explanation
Every `stopEpoch` reverts with `NotAllowed` while the poisoned bucket exists. Consequences:

- All lender funds are frozen — no `stopEpoch` means no epoch rollover, no withdrawal funding (`pendingWithdraws` is only paid at `stopEpoch`), and no claims.
- Trapped duration is unbounded in practice: each time the manager restores `unscaledApr = 0` to unbrick the vault, the attacker can submit a fresh dust-sized `requestWithdraw` in that window to keep the bucket non-zero.
- Quantified loss: the attacker needs only one tranche-token withdrawal request of arbitrary size (e.g., 1 wei worth of tranche tokens; `requestWithdraw(0)` early-returns but any positive `_amount` works, and dust principal suffices since there is no minimum). Against that negligible cost, the entire vault TVL is frozen for as long as the griefing persists, plus realized yield loss for every epoch boundary missed.

### Likelihood Explanation
- Attacker profile fits the allowed model: a KYC-passing lender holding AA/BB tranche tokens calling `IdleCDOEpochVariant.requestWithdraw` → `creditVault.requestWithdraw` → `_requestWithdrawApr0`. No privileged role is abused; the manager's `setApr`/`setAprsWithBuffer` is an honest, routine action.
- Preconditions: an epoch running with `unscaledApr == 0` (the vault explicitly supports this mode via `_requestWithdrawApr0`, `apr0RateByEpoch`, and dedicated tests), and a later APR increase — a normal operational event (rate renegotiation).
- Existing guards do not stop it: `requestWithdraw`'s `lossRecoveryPrice`/`defaultRecoveryFinalized` gates are inapplicable pre-default; `_checkAllowed` only checks KYC and `isEpochRunning`; there is no minimum request size and no path that clears `apr0TotalPrincipal` without a successful `stopEpoch` or a user claim that itself needs an epoch boundary.

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when the APR changed mid-lifecycle. Either:

- settle the APR0 bucket under the new regime (apply `apr0RateByEpoch[epochNumber] = 0` — zero interest — and reset `apr0TotalPrincipal`), or
- track a per-epoch flag (`apr0ActiveByEpoch`) set at request time and use it instead of the live `unscaledApr` to decide settlement, so a later APR change cannot retroactively invalidate outstanding requests.

Add a regression test: deposit → start epoch with APR 0 → `requestWithdraw` → `setApr`/`setAprsWithBuffer` to non-zero → `stopEpoch` must succeed and the user's claim must settle correctly.

### Proof of Concept
Foundry fork PoC outline (against a deployed `IdleCDOEpochVariant` + `IdleCreditVault` pair, mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0GriefBlocksStopEpoch() external {
    // epoch running with unscaledApr == 0
    _stopCurrentEpochWithApr(0);              // manager sets APR 0 for next epoch
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // attacker: KYC'd lender deposits dust and requests withdraw
    uint256 tranches = _depositWithUser(attacker, 1); // 1 wei USDC
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(tranches, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // honest manager raises APR mid-epoch (routine repricing)
    vm.prank(manager);
    strategy.setApr(10e18); // <= maxApr

    // borrower repays; manager tries to stop the epoch
    deal(underlying, borrower, neededFunds, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, expectedInterest);

    // griefing persists: manager restores APR 0 to unbrick,
    // attacker re-requests a dust withdraw -> apr0TotalPrincipal > 0 again
    vm.prank(manager);
    strategy.setApr(0);
    // next APR>0 set -> stopEpoch reverts again: funds remain frozen
}
```

Note: I verified the revert path (`prepareStopEpochWithApr0` at `IdleCreditVault.sol:505-508`, bucket increment at `:567-577`, claim-side clearing at `:811-837`) and the routing at `:285-294`. I was not able to fully verify within this session the exact call site inside `IdleCDOEpochVariant.stopEpoch` that invokes `prepareStopEpochWithApr0`, though the `_onlyIdleCDO` guard and matching test helpers (`_calcApr0StopEpochVals`, assertions on `apr0TotalPrincipal` around `stopEpoch`) confirm it is invoked on the stop path; the PoC should confirm the revert bubbles up through `cdoEpoch.stopEpoch`.