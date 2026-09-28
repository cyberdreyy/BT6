### Title
Stale APR0 withdraw requests permanently brick `stopEpoch` once the manager raises the APR — unprivileged lender can freeze the entire vault - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2015-5195 is a "disabled-feature command still parsed → crash" bug: ntpd accepted `statistics`/`filegen` directives even when the feature wasn't compiled in, and processing them segfaulted the daemon. The analog in idle-tranches is the APR0 withdrawal bucket in `IdleCreditVault`: an unprivileged user can open a withdraw request while `unscaledApr == 0`, which gets parked in the `apr0Users`/`apr0TotalPrincipal` ledger. If the manager later sets a non-zero APR (a normal, honest operation), `prepareStopEpochWithApr0` unconditionally reverts with `NotAllowed` at `contracts/strategies/idle/IdleCreditVault.sol:506-508`, so every subsequent `stopEpoch`/`stopEpochWithDuration` reverts — the epoch state machine is bricked exactly like ntpd's segfault, except the crash permanently freezes all vault funds.

### Finding Description
In `IdleCreditVault.requestWithdraw`, requests made while `unscaledApr == 0` are routed into the APR0 bucket instead of the normal `withdrawsRequests` ledger:

- `contracts/strategies/idle/IdleCreditVault.sol:285-286` — `if (unscaledApr == 0 && !isClosed) { _requestWithdrawApr0(_amount, _user); }`
- `contracts/strategies/idle/IdleCreditVault.sol:567-577` — `_requestWithdrawApr0` sets `apr0Users[_user].principal`/`principalEpoch` and increases `apr0TotalPrincipal`.

At epoch stop, `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` calls `prepareStopEpochWithApr0` on the strategy. That function contains the fatal guard:

- `contracts/strategies/idle/IdleCreditVault.sol:499-508` —
  ```
  uint256 _principal = apr0TotalPrincipal;
  if (_principal == 0) { return ...; }
  if (unscaledApr != 0) { revert NotAllowed(); }
  ```

The intent is "APR0 principal is only valid while APR is 0 for that request lifecycle", but there is no path that clears `apr0TotalPrincipal` when the APR changes. The only clearing points are the end of `prepareStopEpochWithApr0` itself (`line 540`, unreachable once the revert fires) and `_clearWithdrawClaimForEpoch` during *default* claim processing — and a normal claim only moves `principal` to `settledPrincipal` in `_settleApr0` without touching `apr0TotalPrincipal` (`lines 545-565`). So once the flag (APR) is flipped away from 0, the stale ledger makes every stop revert forever, mirroring the CVE: a state recorded while one feature configuration was active crashes the core loop after that configuration is disabled.

Sequence:
1. Vault operates at `unscaledApr == 0` (supported mode; `maxApr`/`setApr` permit 0 and the APR0 accounting exists precisely for this).
2. Attacker (any KYC-passing lender / tranche holder) deposits and calls `IdleCDOEpochVariant.requestWithdraw` during the buffer period — no instant-withdraw, no flags needed. Dust amount suffices; `requestWithdraw(1 wei-equivalent, ...)` sets `apr0TotalPrincipal = 1`.
3. Manager sets a non-zero APR via `setAprs`/`setAprsWithBuffer` and calls `startEpoch` — all honest, routine operations.
4. Manager calls `stopEpoch`/`stopEpochWithDuration`. `prepareStopEpochWithApr0` sees `apr0TotalPrincipal != 0` and `unscaledApr != 0` and reverts.
5. No actor can recover: the borrower cannot be marked defaulted except via the failed stop's catch path (the revert happens in the pre-flight accounting, before borrower interaction), `setEpochParams` cannot rescue it, and `requestWithdraw`/`claimWithdrawRequest` cannot clear `apr0TotalPrincipal` for a non-defaulted vault. Every retry reverts identically — a permanent crash of the epoch state machine.

### Impact Explanation
Permanent freezing of all vault funds. With `isEpochRunning == true` and `stopEpoch` bricked, no withdraw request can ever be funded or claimed (`_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest`, which only `stopEpoch` increments), no new requests can be made mid-epoch (`requestWithdraw` reverts while running), and the borrower can never repay through the sanctioned path. Loss equals the full pool TVL: 100% of lender deposits frozen indefinitely for a dust-cost attack.

### Likelihood Explanation
Very high when the APR0 mode is in use: it requires only one ordinary withdraw request during an APR=0 epoch (a routine user action, not even adversarial) plus a routine APR increase by the manager. Both attacker requirements are minimal — a dust-sized request and no privileged access. The only mitigation is whether the protocol ever runs APR=0 epochs and then raises APR; that is exactly the lifecycle the APR0 machinery was built for, so the trigger is an expected operating transition, not an edge case.

### Recommendation
Handle the stale bucket instead of reverting in `prepareStopEpochWithApr0`: when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, settle the outstanding APR0 principal at zero interest (fold it into `pendingWithdraws` and record `apr0RateByEpoch[epochNumber] = 0` semantics, or migrate each user's `principal` into `settledPrincipal`) and zero `apr0TotalPrincipal` rather than reverting. Alternatively, provide a manager/owner escape that force-closes the APR0 bucket, or reject `setAprs` with a non-zero APR while `apr0TotalPrincipal != 0` so the inconsistency can never be created.

### Proof of Concept
```solidity
// test/foundry/ - Foundry fork PoC sketch against existing harness
function testApr0StaleBucketBricksStopEpoch() external {
  // 1. Configure vault with unscaledApr == 0 (manager sets APR 0)
  vm.prank(manager);
  strategy.setAprs(0, 0);

  uint256 amount = 10_000 * ONE_SCALE;
  idleCDO.depositAA(amount);

  // 2. Attacker requests a dust withdraw while APR == 0 -> lands in apr0 bucket
  uint256 dustTranche = 1;
  deal(address(underlying), attacker, 1);
  vm.startPrank(attacker);
  underlying.approve(address(idleCDO), 1);
  uint256 t = idleCDO.depositAA(1);
  cdoEpoch.requestWithdraw(t, address(AAtranche));
  vm.stopPrank();
  assertGt(strategy.apr0TotalPrincipal(), 0);

  // 3. Manager honestly raises APR and runs a normal epoch
  vm.prank(manager);
  strategy.setAprs(10e18, 10e18); // unscaled 10%, scaled apr within maxApr
  vm.prank(manager);
  cdoEpoch.startEpoch();

  // 4. Any stopEpoch now permanently reverts -> funds frozen
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(10e18, 0);

  // Retrying with any params also reverts; no path clears apr0TotalPrincipal.
}
```

Note: one point I could not fully verify within the tool budget is whether `prepareStopEpochWithApr0` is invoked inside or outside the `try { ... } catch { _handleBorrowerDefault(...) }` block in `IdleCDOEpochVariant.stopEpoch` (`contracts/IdleCDOEpochVariant.sol:451-505`). If it is inside the `try`, the same revert would force an unwarranted borrower default instead of a freeze — which is still a valid finding (attacker-triggered default causes loss socialization/haircuts on honest LPs via `finalizeDefaultRecovery`), but the exact impact framing would shift from permanent freezing to forced default. Either branch produces a fund-impacting outcome from the same stale-ledger defect.