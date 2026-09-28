### Title
Instant-withdraw request griefing permanently blocks `stopEpoch`, freezing all lender funds - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant._stopEpoch` reverts whenever any pending instant-withdraw requests exist (`_pendingInstant() != 0`), and only the owner/manager can clear them via `getInstantWithdrawFunds`, which itself is gated on `block.timestamp >= instantWithdrawDeadline`. A tranche-token holder can repeatedly front-run the manager's `stopEpoch` transaction (or the `getInstantWithdrawFunds` → `stopEpoch` sequence) with a fresh instant-withdraw request of minimal size, keeping `_pendingInstant() != 0` so every epoch-close reverts. The epoch can never be stopped, so normal withdraw requests can never be funded and all LP principal stays locked — mirroring CVE-2026-20065, where an unauthenticated remote party forces the engine into a restart/DoS through packets sent over an established connection (here: unprivileged withdrawal requests sent through the normal epoch flow).

### Finding Description
In `contracts/IdleCDOEpochVariant.sol:338-351`, `_stopEpoch` enforces:

```solidity
_checkNotAllowed(
  !isEpochRunning ||
  block.timestamp < epochEndDate ||
  _pendingInstant() != 0 ||   // any pending instant request bricks the stop
  ...
);
```

`getInstantWithdrawFunds` (lines 558-574) is `onlyOwnerOrManager` and requires `block.timestamp >= instantWithdrawDeadline`, then pulls the pending instant amount from the borrower and sets `allowInstantWithdraw = true`. The attack sequence per epoch:

1. Epoch running, `epochEndDate` reached. Manager broadcasts `stopEpoch(...)`.
2. Attacker (any KYC'd tranche holder) front-runs with `requestWithdraw` of instant type for a dust amount → `_pendingInstant() > 0` → `stopEpoch` reverts.
3. Even after manager calls `getInstantWithdrawFunds` (post-deadline) and pending instant is cleared, the attacker can immediately submit another instant-withdraw request while the epoch is still running and re-block the next `stopEpoch`. Provided instant withdraw requests remain submittable while `isEpochRunning` (gated by `allowInstantWithdraw`/`disableInstantWithdraw` flags rather than a request cutoff at `instantWithdrawDeadline`), this is repeatable every block.
4. Each retry costs the attacker only gas and a dust tranche amount; there is no cooldown or minimum-size check on instant requests in the stop path.

Because `isEpochRunning` stays true: `requestWithdraw`-funded claims never settle, `restoreOperations`/`_beforeUnpause` keep the pause gates constrained (line 636 reverts unpause while `isEpochRunning`), and borrowers' repaid funds cannot be distributed. The invariant broken is liveness of the epoch state machine: a single unprivileged user deterministically forces `stopEpoch` to revert indefinitely.

### Impact Explanation
Temporary freezing of all lender funds. While the epoch cannot be stopped, no normal withdraw receipts are funded via `collectWithdrawFunds` (which is only invoked inside the successful `try` block of `_stopEpoch`, lines 408-410), pending requests cannot be claimed, and deposits remain paused. The freeze persists as long as the attacker keeps front-running (cost: gas only), with no privileged-action remedy short of the manager winning the gas auction every attempt — the borrower, guardian, and queue cannot clear `_pendingInstant()` themselves.

### Likelihood Explanation
Medium. Requirements are minimal: the attacker needs only tranche tokens (obtainable via a normal deposit) and the ability to back-run/front-run manager transactions on a public mempool chain. One caveat I could not fully confirm within the index: whether `requestWithdraw` for the instant type is cut off at `instantWithdrawDeadline` inside `IdleCreditVault.requestWithdraw`. If instant requests are accepted right up to `epochEndDate`, the attack is fully griefing-based and repeatable; if the strategy already rejects instant requests after the deadline, the attack collapses to a one-shot front-run during `[deadline, epochEndDate]` and would not apply. The `_pendingInstant() != 0` revert guard and the owner/manager-only clearing path are confirmed in code.

### Recommendation
Add a request cutoff: reject instant-withdraw `requestWithdraw` calls once `block.timestamp >= instantWithdrawDeadline` (or make the deadline itself the last instant a request can be created), so that after `getInstantWithdrawFunds` settles the pending bucket, `_pendingInstant()` cannot become non-zero again before `stopEpoch`. Alternatively, allow `stopEpoch` to process with pending instant requests by funding them inline (as is done for normal `pendingWithdraws`) rather than reverting.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault.t.sol harness style)
function testInstantWithdrawGriefBlocksStopEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // attacker deposits as normal LP
    _depositWithUser(makeAddr("victim"), amount, true);
    _startEpochAndCheckPrices(0);

    // epoch ends; manager attempts to stop
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // attacker front-runs stopEpoch with a dust instant withdraw request
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // instant type
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector)); // _pendingInstant() != 0
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // after deadline, manager clears pending instant; attacker re-requests instantly
    // (requires instant requests still accepted while isEpochRunning)
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // re-arm the block
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);
    // isEpochRunning stays true; pendingWithdraws never funded -> LP funds frozen
}
```

Note: the PoC's second half depends on the instant-request cutoff behavior in `IdleCreditVault.requestWithdraw`, which I could not fully verify from the indexed snippets; the first half (single-shot revert of `stopEpoch` via `_pendingInstant() != 0`) is directly supported by `contracts/IdleCDOEpochVariant.sol:343-345`.