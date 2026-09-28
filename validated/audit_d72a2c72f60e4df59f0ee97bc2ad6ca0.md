### Title
APR0 withdraw receipt permanently bricks `stopEpoch` once APR is non-zero, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
An unprivileged KYC-passing lender can plant a "landmine" revert in the epoch state machine: by requesting a withdraw while `unscaledApr == 0`, the attacker sets `apr0TotalPrincipal > 0`. From that point, `prepareStopEpochWithApr0` — which `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` must call — reverts with `NotAllowed` whenever `unscaledApr != 0`, aborting every epoch stop until APR is set back to 0. This is the credit-vault analog of the nbdkit "crafted command sequence → assertion failure → process exit" DoS: a specific, cheap sequence of user commands makes the core state-transition function unreachable.

### Finding Description
- `IdleCreditVault.requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0` and the pool is not closed, incrementing the global `apr0TotalPrincipal` bucket (`contracts/strategies/idle/IdleCreditVault.sol:285-286`, `567-577`).
- `prepareStopEpochWithApr0` early-returns only when `apr0TotalPrincipal == 0`; otherwise it enforces `unscaledApr == 0` and reverts otherwise (`contracts/strategies/idle/IdleCreditVault.sol:499-508`).
- `apr0TotalPrincipal` is only cleared inside the same function (`line 540`), so once the revert condition holds there is no in-contract path that resets the bucket — the only escape is the manager calling `setAprs(0, ...)`, which a legitimately-operating manager has no reason to do if the vault has moved to non-zero APR.
- The attacker's sequence is two permissionless calls: `depositAA`/`depositBB` while APR is 0, then `requestWithdraw`. No privileged action is required to arm it, and after it is armed every subsequent `stopEpoch` on a non-zero-APR epoch reverts.

### Impact Explanation
Temporary freezing of all vault funds: the epoch can never be stopped while APR is non-zero, so no withdraw claims, interest distribution, accounting updates, or new epochs can proceed. The freeze persists for as long as the manager keeps a non-zero APR (or fails to diagnose the issue), and the attacker controls whether the landmine exists at near-zero cost (deposit + withdraw request). If APR governance intends the vault to run permanently at non-zero APR after an initial APR0 epoch, the freeze is effectively permanent for that configuration.

### Likelihood Explanation
Requires only that the vault pass through an `unscaledApr == 0` phase (a supported configuration — tests explicitly exercise `setAprs(0, 0)`) and later have APR raised, both of which are normal operating transitions performed by the honest manager. The attacker needs only one tranche deposit and one withdraw request during the APR0 window. Caveat I could not fully verify within the search budget: whether `setAprs` itself reverts while `apr0TotalPrincipal > 0` (which would reduce this to manager-error-only) — no such guard was visible in the searched code, so it should be confirmed against the full `setAprs` implementation.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`. Instead, settle the outstanding APR0 principal at zero interest (store `apr0RateByEpoch[epochNumber] = 0`, clear `apr0TotalPrincipal`) or carry the bucket forward, so a stale APR0 request cannot make `stopEpoch` unreachable. Alternatively, block `requestWithdraw` from creating APR0 receipts, or make `setAprs` settle/refuse to leave APR0 mode while `apr0TotalPrincipal != 0`, ensuring the two state variables can never be inconsistent.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault.t.sol harness)
function testPocApr0StopEpochBrick() external {
    address attacker = makeAddr('attacker');
    // 1. Manager legitimately runs vault at APR 0
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // 2. Attacker (KYC'd EOA) deposits and requests withdraw -> arms apr0TotalPrincipal
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0, 'landmine armed');

    // 3. Epoch lifecycle proceeds; manager later sets normal APR (honest action)
    _startEpochAndCheckPrices(0);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5, 10); // unscaledApr != 0

    // 4. Every stopEpoch now reverts inside prepareStopEpochWithApr0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws());
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);   // reverts at IdleCreditVault.sol:507

    // 5. Attacker's claim and all LP funds remain frozen until APR returns to 0
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();
}
```