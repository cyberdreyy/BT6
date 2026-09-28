### Title
APR change while `apr0TotalPrincipal` is open permanently bricks `stopEpoch`, freezing all vault funds — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. The only place `apr0TotalPrincipal` is reset to zero is *inside* that same function, after the revert check. A lender can open an APR=0 withdraw request while the pool runs at 0% APR, and a subsequent honest APR update by the manager (a normal, permitted operation) makes every later `stopEpoch` call revert forever. There is no recovery path: requests cannot be cancelled and the bucket can never be closed, so the epoch can never be stopped and all underlying plus pending receipts are permanently frozen.

### Finding Description
- During an APR=0 epoch, `requestWithdraw` routes the request into `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `:567-577`).
- `prepareStopEpochWithApr0` (called from `IdleCDOEpochVariant._stopEpoch` at `IdleCDOEpochVariant.sol:362`) reverts if `unscaledApr != 0` while `apr0TotalPrincipal != 0` (`IdleCreditVault.sol:505-508`).
- `apr0TotalPrincipal = 0` is only executed at the end of the same function (`IdleCreditVault.sol:540`), i.e., it can never run once the revert condition holds.
- The manager is legitimately allowed to change APR at any time via `setApr`/`setAprs`/`setAprsWithBuffer`, which write `unscaledApr` (`IdleCreditVault.sol:206-235`). This is the analog of the OpenQ `setPayoutSchedule` issue: the setter mutates storage, but requests already booked under the previous value make the new value inconsistent with payout accounting — the "schedule change" silently invalidates state the payout path depends on, and no code path reconciles it.

### Impact Explanation
Permanent freezing of all vault funds. Once `unscaledApr != 0` and `apr0TotalPrincipal != 0` simultaneously, `stopEpoch`/`stopEpochWithDuration` always revert, so `epochNumber` never increments, borrower principal is never recalled, `pendingWithdraws` are never funded, and `claimWithdrawRequest`/`claimInstantWithdrawRequest` can never mature (they gate on `epochNumber > lastWithdrawRequest`). The entire TVL plus the borrower's outstanding loan is locked with no admin escape (there is no function to clear `apr0TotalPrincipal` or roll back APR).

### Likelihood Explanation
Requires only ordinary actions: (1) an epoch runs with `unscaledApr == 0` (supported mode per the APR0 accounting code); (2) any KYC'd lender submits a normal `requestWithdraw`, opening the APR0 bucket — an unprivileged, allowed action; (3) the manager sets a nonzero APR before the next stop, a routine operation `setApr` explicitly permits ("If manager manually set apr from here..."). No attacker privilege, timing race, or misbehavior is needed; the freeze triggers deterministically on the next `stopEpoch`.

### Recommendation
Decouple the revert from the reset: in `prepareStopEpochWithApr0`, when `unscaledApr != 0`, settle/close the APR0 bucket at zero interest (treat `apr0RateByEpoch` as 0 and zero `apr0TotalPrincipal`) instead of reverting, or alternatively forbid changing `unscaledApr` from 0 to nonzero while `apr0TotalPrincipal != 0` in `setApr`/`setAprs` so the inconsistent state is unreachable.

### Proof of Concept
```solidity
// Foundry fork PoC, based on test/foundry/IdleCreditVault.t.sol harness
function testAprChangeAfterApr0RequestFreezesStopEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Epoch is running with unscaledApr == 0 (APR0 mode)
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0); // stopEpoch(0, ...) previously set APR 0
    // lender (unprivileged) requests withdrawal during APR0 epoch
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // ends epoch, APR stays 0
    cdoEpoch.requestWithdraw(amount, address(AAtranche)); // books apr0TotalPrincipal

    // Manager legitimately raises APR before next epoch starts
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e17, 5e17); // unscaledApr != 0

    // Start next epoch and try to stop it: permanent revert
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
    // apr0TotalPrincipal can never be reset -> every subsequent stopEpoch reverts
}
```