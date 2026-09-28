### Title
APR0 withdraw requests permanently deadlock `stopEpoch` if the manager sets a nonzero APR before epoch settlement - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`prepareStopEpochWithApr0` reverts whenever `apr0TotalPrincipal != 0` while `unscaledApr != 0`. `apr0TotalPrincipal` is only cleared inside that same function, *after* the revert check, so once an APR0 receipt exists and the vault APR is changed to nonzero, the only path that clears the bucket is unreachable. `stopEpoch`/`stopEpochWithDuration` then revert on every call, leaving `isEpochRunning = true` forever: deposits stay paused, withdrawal requests stay disabled, and the epoch can never be settled until the manager sets the APR back to exactly 0. Any KYC-passed lender can create the blocking state by calling `requestWithdraw` during a buffer period while APR is 0; an ordinary honest APR update by the manager then trips the deadlock.

### Finding Description
The external bug class is a deadlock: a lock/state that can never be released because the releasing path is itself blocked. The analog lives in `IdleCreditVault.prepareStopEpochWithApr0`:

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

and the bucket is only zeroed later in the same function:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:539-540
// Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
apr0TotalPrincipal = 0;
```

Any lender can populate the bucket via `requestWithdraw` → `IdleCreditVault.requestWithdraw` → `_requestWithdrawApr0`, which only requires `unscaledApr == 0` and an open pool (`contracts/strategies/idle/IdleCreditVault.sol:285-286`, `567-577`). `unscaledApr` is writable by the manager at any time via `setApr`/`setAprs` (`contracts/strategies/idle/IdleCreditVault.sol:206-235`). `stopEpoch` calls `prepareStopEpochWithApr0` before any other state mutation that could clear the bucket (`contracts/IdleCDOEpochVariant.sol:362`), so the revert is unconditional once both conditions hold. There is no user-side cancel for an APR0 request and no owner/manager escape function that resets `apr0TotalPrincipal`; `setMaxApr`, `setApr`, `transferToken` and the emergency paths do not touch it. The only repair is restoring `unscaledApr` to 0, which may be impossible in practice if the vault legitimately needs a positive APR for the next epoch — and even when possible it is a temporary freeze of the entire vault for the intervening window.

### Impact Explanation
Broken invariant: epoch liveness / guaranteed settlement. While the deadlock persists, `isEpochRunning` remains true, so `_deposit` is paused (startEpoch called `_pause()`), `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are false, `epochNumber` never increments, and every pending receipt stays unclaimable (`_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest`). All LP funds in the vault plus all matured withdraw receipts are frozen. If the next epoch legitimately requires a nonzero APR, the freeze is effectively permanent unless governance accepts a 0-APR epoch purely to unblock settlement. Quantified loss: 100% of vault TVL plus all pending receipts illiquid for the duration of the deadlock; permanently if no 0-APR stop is ever executed.

### Likelihood Explanation
The attacker side is cheap and permissionless for any Keyring-passed lender: one `requestWithdraw` during an APR-0 epoch's buffer period creates `apr0TotalPrincipal != 0`. The honest trigger is routine: the manager calling `setApr`/`setAprs` with a nonzero APR (allowed mid-buffer, `contracts/strategies/idle/IdleCreditVault.sol:225-235`) while preparing the next epoch. No malicious privileged role, no oracle manipulation, and no external dependency is required. The freeze is temporary rather than permanent only if the operator can settle an epoch at APR 0.

### Recommendation
Decouple bucket closure from the APR check in `prepareStopEpochWithApr0`: always run the `apr0TotalPrincipal = 0` settlement (and compute `apr0RateByEpoch` from the realized interest for that epoch) regardless of the new `unscaledApr`, or explicitly settle APR0 receipts at the rate of the epoch in which they were requested rather than reverting when APR changes. Alternatively, forbid `setApr`/`setAprs` transitions from 0 to nonzero while `apr0TotalPrincipal != 0`, or allow APR0 requests to be cancelled/converted into normal receipts so the bucket can always be drained.

### Proof of Concept
Foundry fork outline:

```solidity
// 1. Buffer period, previous epoch had APR 0 (unscaledApr == 0).
//    Attacker (KYC'd lender holding tranche tokens) requests a withdraw.
vm.prank(attacker);
cdo.requestWithdraw(amount, AATranche); // -> apr0Users[attacker].principal > 0, apr0TotalPrincipal > 0

// 2. Honest manager updates APR for the upcoming epoch.
vm.prank(manager);
vault.setAprs(5e18, 5e18 * (epochDuration + bufferPeriod) / epochDuration); // unscaledApr = 5e18

// 3. Epoch starts and runs normally.
vm.prank(manager); cdo.startEpoch();
vm.warp(cdo.epochEndDate() + 1);

// 4. Every stopEpoch attempt reverts in prepareStopEpochWithApr0.
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdo.stopEpoch(newApr, 0);

// 5. Deadlock: isEpochRunning stays true, epochNumber never bumps,
//    no deposit/requestWithdraw/claimWithdrawRequest can proceed.
assertTrue(cdo.isEpochRunning());
vm.expectRevert(); vault_claim_for_attacker; // epochNumber <= lastWithdrawRequest
```

Repeat step 4 arbitrarily: the revert is state-determined, not transient, matching the CVE's "potential deadlock" class — the clearing write (`apr0TotalPrincipal = 0`) sits behind the guard that the stale bucket itself trips.