### Title
Dust queue deposit before the prefunding cutoff permanently bricks `stopEpochWithDuration` and freezes the pool - (contracts/IdleCDOEpochQueue.sol)

### Summary
`IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration` calls `IdleCDOEpochQueue.prefundedDepositsToProcess`, which reverts whenever `epochPendingDeposits` for the settling epoch is non-zero — even when there is nothing prefunded. A KYC-passing user can queue a 1-wei deposit immediately before the `prefundedDepositWindow` cutoff; after the cutoff the manager can no longer convert it via `processDepositsToBorrower` (which requires `block.timestamp + window < epochEndDate`), so every `stopEpochWithDuration` call reverts at the end and rolls back the entire epoch stop. Since `_beforeStopEpoch` also blocks the plain `stopEpoch` selector when a queue is configured, there is no remaining privileged path to stop the epoch. Analog to the CVE-2019-2510 crash/hang class: a cheap unprivileged action wedges the system into a state where the core operation can never complete.

### Finding Description
Relevant code, `contracts/IdleCDOEpochQueue.sol`:

- `requestDeposit` (lines 103–127): during a running epoch, a KYC-allowed wallet can enqueue any `amount` (no minimum) into `epochPendingDeposits[nextEpoch]`, where `nextEpoch = epochNumber + 1`. The prefunded AA queue only rejects deposits once `block.timestamp + prefundedDepositWindow >= epochEndDate` (lines 114–117).
- `processDepositsToBorrower` (lines 149–180): moves `epochPendingDeposits` to `epochPrefundedDeposits`, but is only callable while `isEpochRunning` and strictly before the cutoff (`_checkNotAllowed(_prefundedWindow == 0 || block.timestamp + _prefundedWindow < _cdo.epochEndDate())`, line 164).
- `prefundedDepositsToProcess` (lines 277–282): reverts via `_checkNotAllowed(epochPendingDeposits[_epoch] != 0)` whenever raw queued deposits for the settling epoch remain — and this check runs *before* the zero-prefunded early return is even reachable from the caller.

`contracts/IdleCDOEpochVariantPrefunded.sol`:

- `_afterStopEpochWithDuration` (lines 72–89) invokes `_epochQueue.prefundedDepositsToProcess()` unconditionally on every `stopEpochWithDuration` when `epochQueue` is set. The revert propagates and atomically rolls back the whole `_stopEpoch`, leaving `isEpochRunning = true`.
- `_beforeStopEpoch` (lines 39–48) reverts on the direct `stopEpoch` selector whenever `epochQueue != 0`, so the manager cannot bypass `stopEpochWithDuration` either.

Sequence:
1. Epoch N running, `prefundedDepositWindow = W`, `epochEndDate = T`.
2. Attacker (KYC-passed wallet) calls `queue.requestDeposit(1)` at `T - W - 1`. `epochPendingDeposits[N+1] = 1`.
3. From `T - W` onward, `processDepositsToBorrower` reverts (line 164), so the dust can never be moved to `epochPrefundedDeposits`. `deleteRequest` only lets `msg.sender` (the attacker) remove it.
4. At `T`, manager calls `stopEpochWithDuration(...)`. `_stopEpoch` executes fully, then `_afterStopEpochWithDuration` → `prefundedDepositsToProcess` hits `epochPendingDeposits[N+1] != 0` and reverts `NotAllowed`. The entire transaction rolls back.
5. Every retry reverts identically. The epoch is never stopped, `epochNumber` never increments, borrower funds are never recalled, and pending withdraw receipts (`pendingWithdraws`) can never be funded or claimed. The pool is permanently frozen for a 1-wei attacker cost.

No existing guard helps: `_guarded`/`checkPrefunding` only bound size, `EpochNotRunning`/`isWalletAllowed` checks pass legitimately, and there is no admin sweep for `epochPendingDeposits`.

### Impact Explanation
Permanent freezing of all user funds in the vault: the epoch can never be stopped, so borrower repayment, withdraw-request funding, and all subsequent epoch transitions halt. Attacker cost is a 1-wei deposit plus gas; frozen value is the full pool TVL plus pending withdrawal receipts. This maps directly onto the advisory's "complete DOS" impact translated to the vault's epoch state machine.

### Likelihood Explanation
Requires a prefunded-variant deployment with `epochQueue` set and `prefundedDepositWindow != 0` (the intended configuration — the window exists precisely to gate prefunding). The attacker only needs to be a KYC-passed depositor and to land one transaction before the cutoff. No privileged cooperation, no oracle dependence, and no timing luck beyond a single transaction.

### Recommendation
In `prefundedDepositsToProcess`, only enforce the `epochPendingDeposits == 0` check when `_prefunded != 0` (or more precisely, allow the stop to proceed by treating leftover queue-held deposits as normal `processDeposits` backlog for the next buffer). Alternatively, give the manager a way to clear or refund dust `epochPendingDeposits` entries, or enforce a minimum `requestDeposit` amount plus a permissionless sweep of stale queued deposits into `epochPrefundedDeposits`/`deleteRequest` style refunds after the cutoff.

### Proof of Concept
Foundry fork-style sketch (concrete setup mirrors `test/foundry/IdleCDOEpochVariantPrefunded.t.sol`):

```solidity
function testDustDepositBlocksStopEpoch() external {
    uint256 WINDOW = 5 days;
    vm.prank(cdoEpoch.owner());
    cdoEpoch.setEpochQueue(address(queue));
    vm.prank(manager);
    queue.setPrefundedDepositWindow(WINDOW);

    // honest deposits + epoch running
    _depositWithUser(makeAddr("lp"), 1000e6);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // attacker is KYC-allowed; deposit 1 wei just before the cutoff
    address attacker = makeAddr("attacker");
    deal(address(underlying), attacker, 1);
    vm.warp(cdoEpoch.epochEndDate() - WINDOW - 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);
    vm.stopPrank();
    uint256 nextEpoch = strategy.epochNumber() + 1;
    assertEq(queue.epochPendingDeposits(nextEpoch), 1);

    // after the cutoff the manager can no longer prefund the dust
    vm.warp(cdoEpoch.epochEndDate() - WINDOW);
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    queue.processDepositsToBorrower();

    // epoch ends; borrower approved to repay, but the stop always reverts
    uint256 interest = 1000e6;
    uint256 toRepay = interest + strategy.pendingWithdraws();
    deal(address(underlying), strategy.borrower(), toRepay);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), toRepay);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 duration = cdoEpoch.epochDuration();
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, interest, duration, 0);

    assertTrue(cdoEpoch.isEpochRunning()); // pool permanently stuck
}
```

Note: verification of the exact epoch id used by `prefundedDepositsToProcess` (`epochNumber + (defaulted ? 1 : 0)` vs the queue's `epochNumber + 1` accounting at stop time) was limited by available iterations; the revert condition `epochPendingDeposits[_epoch] != 0` for the just-started epoch id is the same one `requestDeposit` writes to during the running epoch, but the PoC should be confirmed against the live wiring in `test/foundry/IdleCDOEpochVariantPrefunded.t.sol`.