### Title
Unprivileged dust deposit in prefunded AA queue bricks `stopEpochWithDuration` settlement and freezes all vault funds - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
Analogous to the Sablier `CREATE2` revert that stranded counterfactually-prefunded funds, `IdleCDOEpochVariantPrefunded` couples prefunded deposits (underlying already sent to the borrower ahead of settlement) to a settlement path that can be made to revert unconditionally by an unprivileged, KYC-passed lender. Once `epochQueue` is set on the CDO, the direct `stopEpoch` selector is blocked by `_beforeStopEpoch`, so the only way to settle an epoch is `stopEpochWithDuration`, which always calls `_afterStopEpochWithDuration` → `prefundedDepositsToProcess()`. That view reverts whenever `epochPendingDeposits[targetEpoch] != 0` (`IdleCDOEpochQueue.sol:280-281`). Any wallet passing `isWalletAllowed` can create such pending deposits with `requestDeposit(1 wei)` (`IdleCDOEpochQueue.sol:103-127`). When `prefundedDepositWindow == 0` (its default until the manager sets it), those deposits can *never* be forwarded, because `processDepositsToBorrower` reverts unconditionally on `_prefundedWindow == 0` (`IdleCDOEpochQueue.sol:164`), while `requestDeposit` does not block them (`IdleCDOEpochQueue.sol:114-117` — the window check is skipped when the window is zero). Only the attacker can remove the dust via `deleteRequest`. Result: every `stopEpochWithDuration` reverts, the epoch can never be stopped, and the entire pool — borrower-held principal plus all queued withdrawal claims — is frozen.

### Finding Description
- `IdleCDOEpochVariantPrefunded._beforeStopEpoch` reverts on `msg.sig == this.stopEpoch.selector` whenever `epochQueue != 0`, forcing settlement through `stopEpochWithDuration` (`IdleCDOEpochVariantPrefunded.sol:39-48`).
- `stopEpochWithDuration` unconditionally executes `_afterStopEpochWithDuration` (`IdleCDOEpochVariant.sol:520-530`), which calls `prefundedDepositsToProcess()` on the queue.
- `prefundedDepositsToProcess()` reverts if `epochPendingDeposits[_epoch] != 0` (`IdleCDOEpochQueue.sol:277-282`), and `processPrefundedDeposits` additionally reverts if `_prefunded == 0` (`IdleCDOEpochQueue.sol:265`).
- `requestDeposit` only requires `_checkAllowed` (epoch running + Keyring credential) and, for the AA prefunded queue, reverts only when `epochPrefundedDeposits[nextEpoch] != 0` or the nonzero deposit window has been reached (`IdleCDOEpochQueue.sol:111-117`). With `prefundedDepositWindow == 0`, the second clause is dead code, so deposits are accepted until prefunding — which itself is impossible with window 0.
- Even when the manager *does* configure a window, a dust deposit placed before the cutoff that is never swept (e.g., pending while the epoch ends) blocks settlement until the manager manually prefunds it post-`epochEndDate`.
- The revert also poisons the default path: if `_stopEpoch` catches a borrower shortfall via `_handleBorrowerDefault`, `_afterStopEpochWithDuration` still reverts, rolling back the default bookkeeping, so even default processing cannot proceed.

### Impact Explanation
A single 1-wei `requestDeposit` from any KYC'd address freezes the whole credit vault: `stopEpoch`/`stopEpochWithDuration` revert forever, borrower repayments cannot be pulled, `isEpochRunning` stays true, `_beforeUnpause` keeps the contract paused (`IdleCDOEpochVariant.sol:635-637`), and no withdraw requests or claims can be serviced. Quantified loss: the entire vault NAV plus all pending withdrawal receipts are frozen until owner/manager intervention (set a nonzero window, then `processDepositsToBorrower` the dust, then stop) — temporary freezing of 100% of funds; if the attacker deposits from many identities or the queue/CDO config is immutable in a given deployment, the freeze is effectively permanent for that epoch's funds.

### Likelihood Explanation
Preconditions are mild: prefunded variant deployed with `epochQueue` set and `prefundedDepositWindow` unset or zero (the default state after `setEpochQueue`), or simply a dust deposit that escapes the prefunding sweep. The attacker needs only Keyring KYC — an enumerated unprivileged role. Cost is 1 wei of underlying plus gas. The invariant broken is liveness of the epoch state machine: a permissionless queue write can veto a privileged settlement step that was assumed to always succeed.

### Recommendation
- In `requestDeposit`, reject deposits when `prefundedDepositWindow == 0` on prefunded-enabled AA queues (mirror the `processDepositsToBorrower` guard), or auto-reject once the window cutoff passes regardless of configuration.
- Make `_afterStopEpochWithDuration` tolerant: skip (or refund) `epochPendingDeposits` left in the queue at stop time instead of reverting in `prefundedDepositsToProcess`/`processPrefundedDeposits` — i.e., don't let queue state veto epoch settlement, mirroring the report's "don't revert on the counterfactual path" advice.
- Alternatively add a permissionless/manager sweep that refunds leftover pending deposits directly to users during settlement.

### Proof of Concept
Foundry fork PoC (against the existing `IdleCDOEpochVariantPrefunded.t.sol` harness):

```solidity
function testDustDepositBricksStopEpoch() external {
    address attacker = makeAddr("attacker");

    // prefunded queue enabled, deposit window left at default 0
    vm.prank(manager);
    cdoEpoch.setEpochQueue(address(queue));

    // KYC'd attacker queues 1 wei for the next epoch while epoch N runs
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);               // succeeds: window==0 skips cutoff check
    vm.stopPrank();
    uint256 nextEpoch = strategy.epochNumber() + 1;
    assertEq(queue.epochPendingDeposits(nextEpoch), 1);

    // manager cannot forward the dust: processDepositsToBorrower reverts on window==0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    queue.processDepositsToBorrower();

    // epoch ends; settlement is permanently bricked
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // direct stopEpoch is blocked while a prefunded queue is configured
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // stopEpochWithDuration reverts inside prefundedDepositsToProcess (pending != 0)
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), 0);

    assertTrue(cdoEpoch.isEpochRunning()); // epoch can never stop; all NAV frozen
}
```

Caveat: I could not verify `IdleCreditVault.epochNumber` increment semantics line-by-line, but `_prefundedEpochToProcess` (`IdleCDOEpochQueue.sol:288-291`) and the prefunded tests confirm the targeted epoch is `epochNumber + 1` at request time and resolves correctly at settlement; the revert condition (`epochPendingDeposits[_epoch] != 0`) is unconditional regardless of epoch numbering.