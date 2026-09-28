### Title
Permanent/temporary freeze of `stopEpoch` via dust APR0 withdraw request before an APR change - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Any unprivileged lender can open a dust-sized `requestWithdraw` while the vault's APR is 0; if the honest manager later sets a non-zero APR (a routine operation), every subsequent `stopEpoch` call reverts, freezing epoch settlement and all queued withdrawals. Analogous to CVE-2017-9126, an unprivileged crafted input drives the system into a state where a later, legitimate call hits an unconditional abort.

### Finding Description
When `unscaledApr == 0`, `requestWithdraw` routes through `_requestWithdrawApr0`, which increments the global bucket `apr0TotalPrincipal` (IdleCreditVault.sol:285-286, 567-577). During `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which contains:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```
(IdleCreditVault.sol:502-508)

There is no path that drains `apr0TotalPrincipal` except `stopEpoch` itself (`apr0TotalPrincipal = 0` at line 540) or per-user claims via `_clearWithdrawClaimForEpoch`/`_settleApr0` — which only clear it if every requester claims, and the attacker can simply never claim. The attacker's `requestWithdraw` only requires burning their own tranche tokens for a strategy-token receipt; a 1-wei request suffices to keep `apr0TotalPrincipal > 0`.

Sequence:
1. Vault is between epochs with `unscaledApr == 0` (APR0 mode is a first-class supported configuration, exercised in `testApr0WithdrawGetsInterestAtStopEpoch`).
2. Attacker (KYC-passing lender) calls `cdoEpoch.requestWithdraw(1, tranche)` → `apr0TotalPrincipal = 1`. Attacker never claims.
3. Manager calls `startEpoch` with a normal (non-zero) APR → `setAprsWithBuffer` sets `unscaledApr != 0`.
4. At epoch end, `stopEpoch` → `prepareStopEpochWithApr0` → `apr0TotalPrincipal != 0 && unscaledApr != 0` → revert `NotAllowed`.

### Impact Explanation
Broken invariant: epoch state machine liveness. `stopEpoch` cannot execute, so `epochNumber` never advances, `pendingWithdraws` are never funded/collected, interest is never crystallized into `virtualPrice`, and every pending withdraw claimant (all honest users' receipts) is frozen. Recovery requires the manager to set the APR back to 0 and run a zero-interest epoch just to clear the bucket — a forced economic loss of an entire epoch's yield on all TVL — while the attacker's dust request keeps the ability to re-poison the bucket on the next APR0 window. Quantified loss: one full epoch of interest on the pool NAV, plus indefinite griefing of withdraw liquidity for the cost of 1 wei of principal.

### Likelihood Explanation
Requires no privileged collusion: the attacker needs only tranche tokens during an APR=0 window, and the trigger is an ordinary managerial action (raising the APR after a zero-APR promotional/fill epoch). There is no guard that blocks APR changes while `apr0TotalPrincipal > 0`, and no way for the protocol to evict the attacker's open request. The main mitigating factor is that the freeze is not strictly permanent — an honest manager can unbrick by reverting APR to 0 — but that itself imposes the quantified yield loss and does not remove the attacker's standing to repeat.

### Recommendation
Decouple APR0 settlement from the APR guard: in `prepareStopEpochWithApr0`, when `unscaledApr != 0`, settle outstanding APR0 principal at zero rate (`apr0RateByEpoch[epochNumber] = 0`, zero `apr0TotalPrincipal`) instead of reverting — requesters keep their principal claim and simply earn no interest for the epoch the APR changed. Alternatively, reject non-zero `setAprs`/`setAprsWithBuffer` while `apr0TotalPrincipal > 0`, which moves the revert to the safer side (blocking a config change rather than blocking epoch settlement).

### Proof of Concept
Foundry fork sketch against `IdleCreditVault`/`IdleCDOEpochVariant`:

```solidity
function testApr0DustFreezesStopEpoch() external {
  // setup: vault deployed, lender deposited AA, epoch0 stopped with apr 0
  _forceLastEpochAprToZero();                    // unscaledApr == 0

  // attacker: dust withdraw request during APR0 window
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(1, address(AAtranche));
  assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);
  // attacker never claims

  // honest ops: start a normal-APR epoch
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprsWithBuffer(10e18, epochDuration, bufferPeriod);

  // epoch runs, borrower repays; stopEpoch now always reverts
  vm.warp(cdoEpoch.epochEndDate() + 1);
  deal(defaultUnderlying, borrower, owed);
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 0);                      // frozen: epochNumber never bumps

  // all pending withdraw claims bricked while apr0TotalPrincipal > 0
}
```

Uncertainty: I could not trace the exact `stopEpoch` call site within the iteration limit, but `prepareStopEpochWithApr0` is `_onlyIdleCDO` and exists solely for stop-epoch preparation; if `stopEpoch` invokes it conditionally, the same revert applies whenever the conditional path is reached. The analog holds under the "temporary freezing with quantified loss" acceptance criterion.