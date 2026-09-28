### Title
APR0 withdraw request permanently bricks `stopEpoch` once APR is set non-zero, freezing all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` (contracts/strategies/idle/IdleCreditVault.sol:505-508). Any user can open the APR0 bucket with a normal `requestWithdraw` while APR is 0 (IdleCreditVault.sol:285-286 → `_requestWithdrawApr0`, :567-577). Once APR is later raised (a routine, honest manager action via `setAprs`/`setAprsWithBuffer`), every subsequent `stopEpoch` call on `IdleCDOEpochVariant` reverts, because `_stopEpoch` unconditionally calls `prepareStopEpochWithApr0` (IdleCDOEpochVariant.sol:362). This mirrors the mod_dav_lock bug class: an optional, rarely-exercised subsystem (the APR0 accounting module) crashes the entire epoch-close path when its state combination was never expected.

### Finding Description
- An unprivileged KYC'd lender calls `IdleCDOEpochVariant.requestWithdraw` while `unscaledApr == 0`. The strategy burns/mints receipts and increments `apr0TotalPrincipal` (IdleCreditVault.sol:285-286, :574-576).
- The attacker cannot clear `apr0TotalPrincipal` themselves: it is only reset inside `prepareStopEpochWithApr0` (IdleCreditVault.sol:540), which runs inside `stopEpoch`, and `_settleApr0` only moves the user's bucket to `settledPrincipal` after a completed epoch — it does not decrement `apr0TotalPrincipal` (IdleCreditVault.sol:545-565). `requestWithdraw` again while APR>0 just adds to `withdrawsRequests`; the APR0 bucket stays non-zero.
- The owner/manager later sets a non-zero APR via `setApr`/`setAprsWithBuffer` (IdleCreditVault.sol:217-235) — an honest, expected action since APR changes are the normal mechanism for the next epoch.
- From that point, `stopEpoch`/`stopEpochWithDuration` always revert at the `unscaledApr != 0` guard (IdleCreditVault.sol:506-508), before `apr0TotalPrincipal` is cleared. The epoch can never close while APR is non-zero.
- Because `epochEndDate` never advances and `isEpochRunning` stays true, `claimWithdrawRequest` reverts for all users (`epochNumber <= lastWithdrawRequest` at IdleCreditVault.sol:326), new deposits/requests stay gated, and all pending withdraw receipts plus active tranche redemptions are frozen. The only recovery is for the manager to notice and set `unscaledApr` back to 0 — i.e., the protocol's APR can never be raised again as long as any attacker keeps one APR0 receipt open, and each epoch the attacker can re-open the bucket for free during any APR-0 window.

### Impact Explanation
Temporary freezing of all user funds (all pending withdraw claims and the entire pool's epoch progression), attacker-triggerable at the cost of one withdraw request. The freeze persists for as long as APR remains non-zero, and the attacker can re-arm it whenever APR is 0. Quantified: 100% of `getContractValue()` plus `pendingWithdraws` are unclaimable for the freeze duration.

### Likelihood Explanation
Requires only an unprivileged lender (KYC-passing) and an honest manager APR change — both routine. No privileged collusion needed. The guard appears to assume APR0 principal cannot coexist with non-zero APR, but nothing prevents a user from leaving a stale APR0 receipt open across the APR transition.

### Recommendation
In `prepareStopEpochWithApr0`, settle/close the APR0 bucket instead of reverting when `unscaledApr != 0` (e.g., finalize with zero APR0 interest and set `apr0TotalPrincipal = 0`, or compute the split at the stored epoch rate). Alternatively, prevent entering the inconsistent state by reverting `setApr`/`setAprsWithBuffer` while `apr0TotalPrincipal != 0`, or by forcing APR0 requests to settle into normal receipts when APR changes.

### Proof of Concept
Foundry fork PoC sketch (against existing test harness style in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: vault with unscaledApr == 0, attacker is a KYC'd AA lender.
idleCDO.depositAA(attackerDeposit);               // attacker deposits
cdoEpoch.startEpoch(...);                          // epoch 0 running, APR = 0

// Attacker opens an APR0 withdraw request during the running epoch.
vm.prank(attacker);
cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche));
assertGt(IdleCreditVault(strategy).apr0TotalPrincipal(), 0);

// Honest manager raises APR for the next epoch (routine action).
vm.prank(manager);
IdleCreditVault(strategy).setAprsWithBuffer(newApr, duration, buffer);

// Warp past epochEndDate and attempt to close the epoch.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(newApr, 0);

// Every retry reverts while unscaledApr != 0 and apr0TotalPrincipal != 0.
// All users' claimWithdrawRequest calls revert (epochNumber never bumped).
vm.expectRevert(NotAllowed.selector);
cdoEpoch.claimWithdrawRequest();
```

Run: `forge test --match-test testApr0FreezesStopEpoch --fork-url $RPC`.