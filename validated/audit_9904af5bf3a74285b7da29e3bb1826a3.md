### Title
`setEpochQueue` can be called after queue deposits are prefunded to the borrower, bricking `stopEpochWithDuration` and stranding queued depositors' funds — (File: contracts/IdleCDOEpochVariantPrefunded.sol)

### Summary
`IdleCDOEpochVariantPrefunded.setEpochQueue` carries a NatSpec-only operational invariant: "do not change the queue after deposits were already prefunded to the borrower and before `stopEpochWithDuration` settles that epoch" (`IdleCDOEpochVariantPrefunded.sol:22-23`). Nothing in code enforces this. This is the same bug class as M-04: a required call-order/settlement ordering is documented but not enforced, so a single honest-but-forgetful admin action rugs or freezes user funds that were already moved to the borrower.

### Finding Description
The prefunded flow works in two non-atomic steps across two contracts:

1. `IdleCDOEpochQueue.processDepositsToBorrower()` moves `epochPendingDeposits[nextEpoch]` to `epochPrefundedDeposits[nextEpoch]` and transfers the underlying directly to the borrower (`IdleCDOEpochQueue.sol:149-180`). At this point users' funds have left the queue and can no longer be retrieved via `deleteRequest` (it reverts when `epochPrefundedDeposits[_requestEpoch] != 0`, `IdleCDOEpochQueue.sol:187`).
2. Later, `stopEpochWithDuration` → `_afterStopEpochWithDuration` reads the *current* `epochQueue` address, mints AA tranche shares to it via `_mintSharesAtCurrPrice`, mints matching strategy tokens, and calls `processPrefundedDeposits` to settle that epoch in the queue (`IdleCDOEpochVariantPrefunded.sol:72-89`).

Between steps 1 and 2, `setEpochQueue` has no guard:

```solidity
function setEpochQueue(address _epochQueue) external {
    _checkOnlyOwnerOrManager();
    epochQueue = _epochQueue;
}
```
(`IdleCDOEpochVariantPrefunded.sol:24-27`)

If the queue address is changed (or zeroed) while `epochPrefundedDeposits[nextEpoch] != 0` on the old queue:

- If `epochQueue = 0`, `_afterStopEpochWithDuration` returns early (`IdleCDOEpochVariantPrefunded.sol:73-74`). The prefunded deposits are never minted tranche shares and the old queue's `epochPrefundedDeposits` / `epochPrice` are never set, so `claimDepositRequest` permanently reverts (`epochPrice[_epoch] == 0` check, `IdleCDOEpochQueue.sol:375-379`). The underlying is already at the borrower — depositors lose everything or are frozen indefinitely.
- If `epochQueue = newQueue`, `_afterStopEpochWithDuration` calls `processPrefundedDeposits` on the *new* queue, which reverts because `epochPendingDeposits == 0 && epochPrefundedDeposits == 0` there (`IdleCDOEpochQueue.sol:265`). Every `stopEpochWithDuration` reverts → the epoch can never be stopped → all pool funds are frozen until the owner happens to restore the exact old queue address.
- If set to a lookalike queue contract, settlement is recorded against the wrong state, and the prefunded minted tranche tokens plus mirrored strategy tokens are sent to an address with no obligation to distribute them to the actual depositors.

### Impact Explanation
High. Underlying equal to the full prefunded deposit amount has already been transferred to the borrower and is unrecoverable by users (`deleteRequest` is blocked once prefunded). Depositors' tranche-token claims are either permanently unclaimable (queue zeroed/wrong state) or all vault operations are frozen (stopEpoch reverts). Loss scales with total queued deposits for the epoch — potentially the entire AA deposit queue.

### Likelihood Explanation
Low, matching M-04: the privileged roles are honest, but the trigger is a routine admin action — rotating/migrating the queue contract, or disabling it — performed in the wrong order during the multi-day window between `processDepositsToBorrower` and `stopEpochWithDuration`. The invariant lives only in a `@dev` comment, and the setter gives no revert, warning, or flag. An unprivileged user suffers the loss purely by holding a queued deposit during that window; no attacker action is required beyond being a depositor.

### Recommendation
Enforce the invariant in code, e.g. in `setEpochQueue`:

```solidity
// revert if there are unsettled prefunded deposits
_checkNotAllowed(
    epochQueue != address(0) &&
    IIdleCDOEpochQueuePrefunded(epochQueue).prefundedDepositsToProcess() != 0
);
```

or track a `prefundingInFlight` flag on the CDO itself, set by `processDepositsToBorrower`/`checkPrefunding` and cleared in `_afterStopEpochWithDuration`, and gate `setEpochQueue` on it — the same flag-based fix recommended for `batchSettlement`/`setInflowOutflowPool` in M-04.

### Proof of Concept
Foundry fork PoC (prefunded variant + AA queue configured):

```solidity
function testSetEpochQueueDuringPrefundingStrandsDeposits() external {
    // epoch running; user queues an AA deposit
    uint256 amount = 100_000 * ONE_SCALE;
    _queueDepositAA(user, amount);               // requestDeposit during running epoch

    // owner/manager prefunds queued deposits to the borrower
    vm.prank(manager);
    queue.processDepositsToBorrower();           // underlying now sits at borrower
    assertGt(queue.epochPrefundedDeposits(strategy.epochNumber() + 1), 0);

    // owner migrates queue (or sets to 0) — documented as unsafe, but not enforced
    vm.prank(owner);
    cdo.setEpochQueue(address(0));               // NO revert — invariant only in NatSpec

    // warp past epoch end and stop
    vm.warp(cdo.epochEndDate() + 1);
    vm.prank(manager);
    cdo.stopEpochWithDuration(newApr, interest, duration, 0);

    // _afterStopEpochWithDuration early-returns: prefunded epoch never settled
    assertEq(queue.epochPrice(strategy.epochNumber() + 1), 0);

    // depositor can never claim tranche tokens nor delete the request
    vm.expectRevert();                           // epochPrice == 0 -> NotAllowed
    queue.claimDepositRequest(strategy.epochNumber() + 1);
    vm.expectRevert();                           // epochPrefundedDeposits != 0 -> NotAllowed
    queue.deleteRequest(strategy.epochNumber() + 1);

    // variant: set a fresh queue instead of 0 -> stopEpochWithDuration always reverts
    // (processPrefundedDeposits on the new queue reverts: _prefunded == 0),
    // freezing the entire pool until the old queue address is restored.
}
```

Relevant code: `IdleCDOEpochVariantPrefunded.sol:22-27`, `:72-89`; `IdleCDOEpochQueue.sol:149-180`, `:250-270`, `:373-389`.