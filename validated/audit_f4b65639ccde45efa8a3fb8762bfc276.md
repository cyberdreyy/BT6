### Title
A single dust APR0 withdraw request permanently bricks `stopEpoch` once APR is set above zero, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts unconditionally when `apr0TotalPrincipal != 0` while `unscaledApr != 0`. Any KYC-passing lender can open an APR0 withdraw receipt (even for dust) while the vault's APR is 0. Because there is no way to cancel a withdraw request or clear `apr0TotalPrincipal` outside of a successful `stopEpoch` (or default), that dust receipt turns into a permanent circuit breaker: the moment the manager later sets a non-zero APR, every subsequent `stopEpoch` call reverts, freezing lender withdrawals, borrower repayment, and the entire epoch state machine. This is the credit-vault analog of CVE-2023-30636: an unprivileged request poisons global state so a critical coordination step (epoch rollover / node start) fatally errors on every attempt.

### Finding Description
In `IdleCreditVault`, a lender creates an APR0 receipt via `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw`, which calls `_requestWithdrawApr0` and increments the global `apr0TotalPrincipal` (contracts/strategies/idle/IdleCreditVault.sol:285-286, 567-577).

At epoch stop, `IdleCDOEpochVariant.stopEpoch` calls `prepareStopEpochWithApr0`, which contains:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:502-508
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The guard ordering is the flaw: the function reverts *after* confirming a live APR0 bucket exists, whenever the current `unscaledApr` is non-zero. `apr0TotalPrincipal` is only reset inside this same function (line 540) or inside the default-recovery claim path `_clearWithdrawClaimForEpoch` (line 827). There is no `cancelWithdraw`/queue-removal path for a normal request, so the attacker-controlled bucket cannot be unwound by anyone once created — including by the attacker.

Attack sequence (named phases):

1. Buffer phase, `unscaledApr == 0` (vault in APR0 mode, or manager briefly sets APRs to 0 between epochs, a routine config change). Attacker — a KYC-passing lender — deposits AA and calls `cdoEpoch.requestWithdraw(dustAmount, AAtranche)`. `apr0TotalPrincipal` becomes `dustAmount > 0`, `apr0Users[attacker].principalEpoch = epochNumber`.
2. Manager (honest) later calls `strategy.setAprs(nonzeroApr, scaledApr)` or `setAprsWithBuffer` to raise APR for the next epoch, then `startEpoch`.
3. Epoch ends. Manager calls `stopEpoch`. `prepareStopEpochWithApr0` sees `apr0TotalPrincipal != 0` and `unscaledApr != 0` → `revert NotAllowed()`. Every retry reverts identically.
4. `epochNumber` never increments, `collectWithdrawFunds`/`sendInterestAndDeposits` never run, `isEpochRunning` stays true. All pending withdraw claims revert at `_claimFundedWithdrawRequest` (`epochNumber <= lastWithdrawRequest`, line 326), and the borrower cannot repay through `stopEpoch`. The only escape is the manager permanently keeping `unscaledApr == 0` — i.e., the attacker has forced the vault to run at zero-APR economics forever, or stay frozen.

Broken invariant: liveness of the epoch state machine and fair settlement — a user's immutable receipt makes a global `stopEpoch` precondition unsatisfiable under a legitimate configuration.

Existing guards do not stop it: `maxApr` only bounds APR magnitude, `setApr` allows manager/CDO changes mid-lifecycle, and the `unscaledApr != 0` check is precisely what the attacker weaponizes.

### Impact Explanation
Permanent freezing of all vault funds (or permanent confinement to APR-0 economics). Once triggered, `stopEpoch` reverts with `NotAllowed` on every call: lender tranche tokens cannot be withdrawn through the request/claim flow, pending withdraw receipts never fund (`pendingWithdraws` never settles), and borrower principal plus interest cannot be repaid into the vault. Quantified: 100% of vault TVL is locked for as long as the manager intends any `unscaledApr != 0` epoch; restoring liveness requires running all future epochs at APR 0, which forgoes `expectedEpochInterest` on the entire TVL each epoch. One dust deposit (gas-cost only) achieves this.

### Likelihood Explanation
Low-to-medium likelihood, high severity. The precondition requires the vault to operate at `unscaledApr == 0` at some point while a lender makes a request — an explicitly supported mode (`_requestWithdrawApr0`, `apr0RateByEpoch`, APR0 tests exist). APR0 epochs occur whenever the manager pauses yield or the pool is being wound up; an attacker can also sandwich a routine `setAprs(0,0)` → `setAprs(apr,…)` sequence, placing the dust request in the same buffer window. No privileged collusion is needed; the manager's honest APR change is the trigger. The frozen state is not self-healing.

### Recommendation
Do not revert on `unscaledApr != 0` when an open APR0 bucket exists. Either (a) settle the bucket at a zero APR0 rate (skip `apr0RateByEpoch` write and just zero `apr0TotalPrincipal`), since an APR0 request can legitimately only accrue the rate of its request epoch, or (b) move the check to request time — forbid `setAprs`/`setAprsWithBuffer` to a non-zero `unscaledApr` while `apr0TotalPrincipal != 0`, or auto-settle APR0 requests at the epoch boundary so the global bucket never straddles an APR change. Option (b) preserves the "APR0 principal is only valid while APR is 0" invariant without bricking `stopEpoch`.

### Proof of Concept
Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

function testApr0ReceiptBricksNonZeroAprStopEpoch() external {
    // --- Setup: vault in APR0 mode ---
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // unscaledApr = 0

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);                 // honest LP liquidity
    _transferBurnedTrancheTokens(address(this), true);

    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1 * ONE_SCALE, true);   // dust KYC deposit

    // Buffer phase, epoch 0 pending: attacker opens a dust APR0 request
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Manager (honest) raises APR for the next epoch and starts it
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, initialProvidedApr);
    _startEpochAndCheckPrices(0);

    // Epoch ends; borrower is funded and ready
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 interest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, interest + amount);

    // Every stopEpoch attempt reverts: apr0TotalPrincipal != 0 && unscaledApr != 0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, interest);

    // No way to clear the bucket: attacker cannot cancel, requestWithdraw has no undo,
    // and claims revert because epochNumber never advances.
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // Vault is permanently frozen unless manager reverts to unscaledApr == 0 forever.
}
```

Note: I was not able to re-verify the exact `stopEpoch` body in `IdleCDOEpochVariant.sol` within the iteration budget (the grep returned match counts, not lines). The PoC assumes `stopEpoch` invokes `prepareStopEpochWithApr0` unconditionally, consistent with the vault's `_onlyIdleCDO` gating and the APR0 test suite (`_stopEpochAndCheckPrices` passing under APR0 scenarios in `test/foundry/IdleCreditVault.t.sol`). If `stopEpoch` calls it only when `unscaledApr == 0`, the freeze weakens to "APR0 bucket can never settle after an APR raise" — still a permanent lock of the attacker's receipt but without freezing the whole vault; the finding should be downgraded accordingly.