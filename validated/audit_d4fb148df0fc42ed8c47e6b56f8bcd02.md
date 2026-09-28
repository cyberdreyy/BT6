### Title
APR0 withdraw receipt permanently bricks `stopEpoch` once APR is raised — unprivileged lender can seed an irreversible pool freeze - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` (lines 505–508). `apr0TotalPrincipal` is created by any lender calling `requestWithdraw` while `unscaledApr == 0` (lines 285–286 → `_requestWithdrawApr0`), and it is only cleared inside `prepareStopEpochWithApr0` itself (line 540) — the very function that reverts once APR is non-zero. The CDO calls `prepareStopEpochWithApr0` on every `stopEpoch`/`stopEpochWithDuration`. Therefore, if APR is raised while any APR0 receipt is still open, every subsequent `stopEpoch` reverts, `epochNumber` can never advance, `claimWithdrawRequest` keeps reverting on `epochNumber <= lastWithdrawRequest` (line 326), and all vault funds are permanently frozen.

### Finding Description
An unprivileged KYC-passed lender performs the seeding transaction during a `unscaledApr == 0` epoch: deposit AA, then `cdoEpoch.requestWithdraw(0, AATranche)`. Because `unscaledApr == 0`, the vault takes the `_requestWithdrawApr0` branch (lines 285–286), mints the user a receipt and increments `apr0TotalPrincipal`. This bucket survives across epochs: `prepareStopEpochWithApr0` only zeroes it at line 540 on a successful stop, and `_settleApr0`/`_clearWithdrawClaimForEpoch` only decrement the *user* bucket; even after a user's own principal settles, a fresh dust `requestWithdraw` re-creates `apr0TotalPrincipal` for as long as APR stays 0.

The freeze triggers when the manager later calls `setAprs(nonZero)` (a routine, honest operation to re-enable yield) while `apr0TotalPrincipal != 0`. From that point `prepareStopEpochWithApr0` hits:

```solidity
// IdleCreditVault.sol:505-508
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

on every stop attempt. There is no escape path: `apr0TotalPrincipal` cannot be decremented by claims (`_settleApr0` only settles per-user buckets; the global bucket is only closed at line 540 inside the reverting function), and `stopEpoch` cannot succeed without clearing it. The epoch can never stop, `epochNumber` never increments, and `_claimFundedWithdrawRequest` permanently reverts for every pending withdrawer.

### Impact Explanation
Permanent freezing of all funds: tranche holders cannot request or claim withdrawals (new requests mint receipts but can never be claimed since `epochNumber` stays fixed), instant withdraws are frozen, and the borrower can never repay into a stopped epoch. Loss magnitude equals total pool TVL plus all funded-but-unclaimed withdraw receipts. The attacker seeds the bricking condition for the cost of a dust deposit+withdraw and can keep re-seeding `apr0TotalPrincipal` each epoch to make any APR raise fatal.

### Likelihood Explanation
Requires a pool operating at `unscaledApr == 0` (a supported mode — `_requestWithdrawApr0` and `apr0RateByEpoch` exist precisely for it) and a subsequent manager APR increase, which is the natural remediation a manager would attempt to restart yield — exactly when it is fatal. The attacker component is fully unprivileged and costs only dust. Partially mitigated by the honest-manager trigger (the manager could avoid raising APR), but the code offers no guard, warning, or recovery once it happens.

### Recommendation
Allow `prepareStopEpochWithApr0` to finalize the APR0 bucket when `unscaledApr != 0` by settling accrued interest at the last recorded `apr0RateByEpoch` (or zero interest) rather than reverting, or block `setAprs` to non-zero while `apr0TotalPrincipal != 0` and provide a manager escape to force-close the bucket.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// 1. Pool at unscaledApr == 0 (setAprs(0,0) already applied by manager).
// 2. Attacker (KYC'd lender):
idleCDO.depositAA(1e6);
cdoEpoch.requestWithdraw(0, AAtranche);          // seeds apr0TotalPrincipal
// 3. Honest epoch cycles run while APR stays 0; attacker may re-seed dust each epoch.
// 4. Manager re-enables yield:
vm.prank(manager);
IdleCreditVault(strategy).setAprs(apr, scaledApr); // unscaledApr != 0 now
// 5. Any stopEpoch permanently reverts:
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(apr, 0);
// 6. Victim's funded claim is frozen forever:
vm.prank(victim);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.claimWithdrawRequest();
```

Note: I could not fully trace `stopEpoch`'s internal call to `prepareStopEpochWithApr0` and `setAprs` gating within the available iterations; the finding rests on `IdleCreditVault.sol:285–295, 356–375, 490–541` and the revert chain described above.