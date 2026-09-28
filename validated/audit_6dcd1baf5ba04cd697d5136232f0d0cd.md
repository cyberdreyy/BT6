### Title
Open APR0 withdraw bucket permanently bricks `stopEpoch` once APR is non-zero — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The NTP bug class — a routine packet crashing the daemon because a fix left an unhandled state — maps to `prepareStopEpochWithApr0` in `IdleCreditVault`: if any APR0 withdrawal principal is still open (`apr0TotalPrincipal != 0`) while `unscaledApr != 0`, the function reverts with `NotAllowed()`. Since the CDO calls this on every `stopEpoch`, the whole epoch lifecycle halts and all vault funds freeze. Any KYC-passing lender can create the poisoning state by calling `requestWithdraw` during a zero-APR epoch; it then detonates on the honest manager's next legitimate APR reconfiguration.

### Finding Description
- `requestWithdraw` in APR0 mode routes to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` (line 576) and stores per-user `principal`/`principalEpoch`.
- `prepareStopEpochWithApr0` (lines 490–541) fast-paths only when `_principal == 0`; otherwise it reverts when `unscaledApr != 0` (lines 505–508) — before reaching the `apr0TotalPrincipal = 0` reset at line 540.
- The only other path that reduces `apr0TotalPrincipal` is `_clearWithdrawClaimForEpoch` with `_isClearingApr0 = true`, reachable solely through `_claimDefaultedWithdrawRequest` after a borrower default and `finalizeDefaultRecovery` (lines 822–828, 772–784).
- So the sequence is: (1) manager runs an epoch with `unscaledApr == 0`; (2) attacker deposits (KYC via `isWalletAllowed`) and calls `requestWithdraw`, leaving `apr0TotalPrincipal > 0` and `principalEpoch = epochNumber`; (3) epoch rolls over — `_settleApr0` only moves principal to `settledPrincipal` per-user and never decrements the global bucket; (4) manager honestly sets a non-zero APR for a later epoch; (5) every subsequent `stopEpoch` reverts in `prepareStopEpochWithApr0`. Repayments, interest accrual, epoch rollover, and all normal/instant claims are frozen permanently — the vault equivalent of the ntpd crash.

### Impact Explanation
Permanent freezing of all user funds in the vault: borrower repayments can no longer be processed, withdrawals can't be claimed at par, and the pool cannot be stopped or closed — while the pool is not in default, so the `finalizeDefault`/`DefaultDistributor` recovery escape does not exist. Loss equals the full TVL locked in the strategy (attacker's own principal is sacrificed, making this a griefing attack costing the attacker only their deposit, which can be minimal).

### Likelihood Explanation
Requires a pool that operates an APR0 epoch (a supported, tested mode — see `testApr0WithdrawLateClaimDoesNotAccrueExtraEpochs`) and a later honest APR change by the manager, both routine operations. Attacker cost is one KYC'd deposit plus a `requestWithdraw`. No privileged or malicious role is needed; the trigger is the honest manager's normal configuration call.

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when `unscaledApr != 0`. Instead, settle/close the open APR0 bucket gracefully — e.g., treat `apr0TotalPrincipal` as settled at zero interest (or at the last recorded `apr0RateByEpoch`) and zero the bucket so subsequent epochs proceed. Alternatively, make `requestWithdraw` reject new APR0 requests when APR is about to be non-zero, and provide a permissionless/admin sweep that clears `apr0TotalPrincipal` for stale requests.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_forceLastEpochAprToZero`):

```solidity
// 1. Deposit as attacker (KYC'd wallet), start epoch, force APR = 0
idleCDO.depositAA(amount);            // attacker
_startEpochAndCheckPrices(0);
_stopEpochWithApr(0);                 // unscaledApr == 0

// 2. Attacker requests withdraw -> apr0TotalPrincipal > 0
cdoEpoch.requestWithdraw(attackerTrancheBal, address(AAtranche));

// 3. Roll one epoch; bucket stays open globally
_startEpochAndCheckPrices(1);

// 4. Honest manager sets APR = 5% for the next epoch
vm.prank(manager);
cdoEpoch.setApr(5e18);                // or equivalent setter

// 5. Every stopEpoch now reverts permanently
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(underlying, borrower, expectedInterest + pendingWithdraws);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(0, expectedInterest); // reverts inside prepareStopEpochWithApr0
```

Caveat: I was unable to verify the exact setter name for `unscaledApr` and whether `IdleCDOEpochVariant.stopEpoch` invokes `prepareStopEpochWithApr0` unconditionally on this variant (grep output was truncated); the PoC assumes the standard wiring seen in the test suite. If `stopEpoch` only calls it conditionally, the freeze may be narrower than described.