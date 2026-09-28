### Title
`setAprs()`/`setAprsWithBuffer()` reverts `stopEpoch` for APR=0 withdrawal receipts, freezing user funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a privileged config change (`setRedeemable()`) that strands users who already locked into a redemption path. The analog in this repo is the ungated APR setter on `IdleCreditVault`: users who open withdraw requests while `unscaledApr == 0` create `apr0Users`/`apr0TotalPrincipal` accounting that only settles if APR is still 0 at `stopEpoch`. Any later honest APR change makes `prepareStopEpochWithApr0` revert, blocking `stopEpoch` and freezing all pending withdrawals.

### Finding Description
When a user calls `requestWithdraw` while `unscaledApr == 0`, the vault records the request in the APR0 bucket instead of the normal one:

- `requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0`, incrementing `apr0Users[_user].principal` and `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `567-577`).
- At epoch end, the CDO calls `prepareStopEpochWithApr0`, which hard-reverts if the global APR0 bucket is non-empty and `unscaledApr != 0` (`IdleCreditVault.sol:499-508`):
  ```solidity
  uint256 _principal = apr0TotalPrincipal;
  if (_principal == 0) return (...);
  if (unscaledApr != 0) {
    revert NotAllowed();
  }
  ```
- `setApr`, `setAprs`, and `setAprsWithBuffer` can be called by the CDO or manager with no check on `apr0TotalPrincipal` or pending APR0 receipts (`IdleCreditVault.sol:206-235`). There is no guard preventing the manager (or the CDO during `startEpoch` APR scaling) from moving `unscaledApr` off zero while APR0 receipts are outstanding.

Attack sequence (unprivileged user + honest privileged calls):
1. Pool is in buffer phase with `unscaledApr == 0`. Attacker deposits and calls `requestWithdraw` via the CDO, creating a nonzero `apr0TotalPrincipal`.
2. Honest manager configures the next epoch's APR to a nonzero value via `setAprs`/`setAprsWithBuffer` (a routine operation; `startEpoch` scaling uses the same path).
3. Honest manager calls `stopEpoch` at epoch end. It invokes `prepareStopEpochWithApr0`, which reverts `NotAllowed` because `apr0TotalPrincipal != 0` and `unscaledApr != 0`.
4. `stopEpoch` can never succeed while APR is nonzero; every claim path (`claimWithdrawRequest` → `_claimFundedWithdrawRequest` → `_settleApr0`) is gated on `epochNumber` advancing past `lastWithdrawRequest`, which only `stopEpoch` does. All pending receipts — the attacker's and every other user's — are frozen until APR is set back to 0.

### Impact Explanation
Temporary freezing of all user funds in the vault: pending withdraw receipts cannot be claimed, and no epoch can close, while `unscaledApr != 0` coexists with open APR0 requests. Loss magnitude equals the entire pending-withdrawal basis plus active TVL locked in the running epoch. It is resolved only if the owner/manager deliberately resets APR to 0, so it is not permanent, but there is no on-chain guard or warning preventing the misconfiguration.

### Likelihood Explanation
Medium-low. It requires the APR0 mode to be used (APR set to 0 for an epoch, which the codebase explicitly supports via the `apr0Users` flow) and a subsequent APR increase before `stopEpoch`. That is a plausible operational sequence — e.g., a zero-interest buffer epoch followed by a normal epoch — and no code prevents it. The trigger is an honest manager action, so the attacker does not control timing; the attacker only needs an open APR0 receipt when it happens.

### Recommendation
- In `setApr`/`setAprs`/`setAprsWithBuffer`, revert if `apr0TotalPrincipal != 0` and the new `unscaledApr != 0`, mirroring the existing `prepareStopEpochWithApr0` invariant; or
- Have `prepareStopEpochWithApr0` settle the APR0 bucket at a zero rate instead of reverting when APR changed mid-lifecycle (documenting the intended semantic for APR0 receipts under a nonzero APR epoch).

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
// Foundry fork test sketch against deployed IdleCDOEpochVariant + IdleCreditVault.
// Assumes helpers: deal underlying to `attacker`, KYC passed, owner/manager prankable.

function test_Apr0ReceiptFreezesStopEpochOnAprChange() public {
    // 1. Buffer phase, APR == 0
    vm.prank(manager);
    creditVault.setAprs(0, 0);            // unscaledApr = 0

    // 2. Attacker deposits and requests withdraw -> APR0 bucket populated
    deal(address(underlying), attacker, 1000e6);
    vm.startPrank(attacker);
    underlying.approve(address(cdo), 1000e6);
    cdo.depositAA(1000e6);
    cdo.withdrawAA(amount);               // requestWithdraw -> _requestWithdrawApr0
    vm.stopPrank();
    assertGt(creditVault.apr0TotalPrincipal(), 0);

    // 3. Epoch runs, buffer + duration pass
    vm.prank(manager); cdo.startEpoch();
    skip(epochDuration + bufferPeriod);

    // 4. Honest manager sets next epoch APR to nonzero
    vm.prank(manager);
    creditVault.setAprsWithBuffer(5e18, epochDuration, bufferPeriod);

    // 5. stopEpoch permanently reverts -> attacker (and all pending receipts) frozen
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdo.stopEpoch();

    // 6. Attacker cannot claim: epochNumber never advanced past lastWithdrawRequest
    vm.prank(attacker);
    vm.expectRevert(NotAllowed.selector);
    cdo.claimWithdrawRequest();
}
```

Note: this was reasoned from `IdleCreditVault.sol` (`requestWithdraw`/`_requestWithdrawApr0`/`prepareStopEpochWithApr0`/`setApr*`); I was not able to view the exact `stopEpoch` call site in `IdleCDOEpochVariant.sol` to confirm `prepareStopEpochWithApr0` is invoked unconditionally there — if it is only called in some modes, the freeze applies only to those modes.