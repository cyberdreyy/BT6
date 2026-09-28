### Title
Stored absolute `epochEndDate`/`instantWithdrawDeadline` cannot be shortened when `epochDuration`/`instantWithdrawDelay` are reduced, freezing emergency stop and instant-withdraw funding — ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`startEpoch` snapshots absolute end times (`epochEndDate = block.timestamp + epochDuration`, `instantWithdrawDeadline = block.timestamp + instantWithdrawDelay`), while `stopEpoch` and `getInstantWithdrawFunds` gate on those stored values. Reducing the duration parameters afterward has no effect, so the manager cannot accelerate epoch termination or instant-withdraw funding in an emergency — the same bug class as storing `cooldownEnd` instead of `cooldownStart`.

### Finding Description
At `startEpoch`, the contract stores absolute deadlines derived from the parameters in force at that moment:
- `epochEndDate = block.timestamp + _epochDuration` (line 266)
- `instantWithdrawDeadline = block.timestamp + instantWithdrawDelay` (line 268)

Later gates use the stored values, not the current parameters:
- `stopEpoch`/`stopEpochWithDuration` revert while `block.timestamp < epochEndDate` (line 342).
- `getInstantWithdrawFunds` reverts while `block.timestamp < instantWithdrawDeadline` (line 561).
- `depositDuringEpoch` is likewise blocked only until the stored `epochEndDate` (line 667).

`setEpochParams` (called via `stopEpochWithDuration`, line 525, and directly by owner/manager) mutates `epochDuration`/`bufferPeriod` but never touches `epochEndDate` for the running epoch; likewise there is no path to recompute `instantWithdrawDeadline` when `instantWithdrawDelay` is changed. Concretely:

1. Manager calls `startEpoch` with `epochDuration = 30 days`, `instantWithdrawDelay = 3 days`. A lender has requested an instant withdraw (APR-drop path in `requestWithdraw`, lines 761-769) and `pendingInstantWithdraws > 0`.
2. A solvency/market emergency requires recalling principal immediately (e.g., close the pool via `stopEpochWithDuration(_, 1, 0, _)` or set `epochDuration = 0`).
3. `_stopEpoch` reverts at line 342 until the stale `epochEndDate`; `getInstantWithdrawFunds` reverts at line 561 until the stale `instantWithdrawDeadline`. Instant-receipt holders burned their tranche tokens already (`requestInstantWithdraw` burns strategy tokens and tranche tokens), and `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are false during the epoch (lines 249-250), so they have no alternative exit path — asymmetrically worse than users who never requested.

The broken invariant: parameter changes intended to accelerate settlement do not propagate to already-stored absolute deadlines, so the emergency "reduce duration to zero" escape hatch is inert for the in-flight epoch.

### Impact Explanation
Temporary freezing of all pool funds (every lender's principal plus pending instant-withdraw receipts) for up to `epochEndDate - block.timestamp` (full epoch duration in the worst case). Pending instant withdrawals cannot be funded early even if the borrower is willing to repay; the pool cannot be stopped early to trigger `_interest == 1` close-out or `_handleBorrowerDefault` recovery accounting. Quantified loss: up to the entire NAV for the remaining epoch duration.

### Likelihood Explanation
Trigger requires only an honest owner/manager attempting a legitimate emergency action (shorten `epochDuration`/`instantWithdrawDelay` or close the pool) mid-epoch — exactly the scenario the report describes (set cooldown to 0 in a crisis). No attacker action is needed; the freeze is a direct consequence of the stale stored timestamp. Any epoch with `epochDuration > 0` is exposed.

### Recommendation
Store the epoch start time and compute the deadline relative to the current parameter at check time, mirroring the report's fix:
- Replace the absolute check in `_stopEpoch` with `block.timestamp < epochStart + epochDuration` (store `epochStart` at `startEpoch` instead of `epochEndDate`), so reducing `epochDuration` immediately enables `stopEpoch`.
- Similarly gate `getInstantWithdrawFunds` on `epochStart + instantWithdrawDelay` so shortening `instantWithdrawDelay` unblocks funding.
- Alternatively, make `setEpochParams` (and any `instantWithdrawDelay` setter) explicitly clamp `epochEndDate`/`instantWithdrawDeadline` to `min(stored, block.timestamp + newDuration)` for the running epoch.

### Proof of Concept
```solidity
function testEpochEndDateNotShortened() public {
    // deposits, epochDuration = 30 days, instantWithdrawDelay = 3 days
    idleCDO.depositAA(10_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);

    uint256 endDate = cdoEpoch.epochEndDate();
    assertGt(endDate, block.timestamp);

    // Emergency: manager tries to shorten the running epoch
    vm.prank(manager);
    cdoEpoch.setEpochParams(0, cdoEpoch.bufferPeriod());

    // Cannot stop the epoch early: stored epochEndDate still gates
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1); // close-pool request reverts

    // Even instant-withdraw funding stays blocked by the stale deadline
    // if pendingInstantWithdraws > 0 and instantWithdrawDelay was raised
    // then lowered, getInstantWithdrawFunds still requires the stored
    // instantWithdrawDeadline (line 561).

    // Funds only become releasable at the original end date
    vm.warp(endDate);
    deal(defaultUnderlying, borrower, /* grossPrincipal + interest */);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1); // now succeeds
}
```

Uncertain: whether `setEpochParams` is callable while `isEpochRunning` and whether `instantWithdrawDelay` has a dedicated setter — the index did not show those bodies; the freeze holds regardless since neither stored deadline is recomputed by any code path visible in `contracts/IdleCDOEpochVariant.sol` lines 233-574.