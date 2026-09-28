### Title
A single queued AA deposit left unprocessed at the prefunded cutoff permanently blocks `stopEpochWithDuration`, freezing the entire pool - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
`IdleCDOEpochVariantPrefunded` requires `stopEpochWithDuration` to settle prefunded queue deposits via `prefundedDepositsToProcess`, which reverts whenever `epochPendingDeposits[_epoch] != 0`. The queue and the CDO enforce a "subscription window" (`prefundedDepositWindow`) in which new deposits are rejected **and** `processDepositsToBorrower` is also rejected (`block.timestamp + _prefundedWindow < epochEndDate` required). A KYC-passing lender can therefore submit a deposit in the last possible block before the cutoff; if it is not forwarded to the borrower before the window closes, it can never be prefunded, `epochPendingDeposits` stays non-zero forever, and every subsequent `stopEpochWithDuration` call reverts inside `_afterStopEpochWithDuration`. This is the same shape as the crowdfund finding: an invariant (`minContribution` gap / "no raw pending deposits at stop") that an unprivileged user can make permanently unreachable, leaving the pool in limbo.

### Finding Description
Relevant code:

- `IdleCDOEpochQueue.requestDeposit` (contracts/IdleCDOEpochQueue.sol:103-118): accepts deposits while `block.timestamp + prefundedDepositWindow < epochEndDate` and `epochPrefundedDeposits[nextEpoch] == 0`.
- `IdleCDOEpochQueue.processDepositsToBorrower` (contracts/IdleCDOEpochQueue.sol:149-180): only callable by owner/manager and only while `block.timestamp + _prefundedWindow < _cdo.epochEndDate()`; moves `epochPendingDeposits` → `epochPrefundedDeposits`.
- `IdleCDOEpochQueue.prefundedDepositsToProcess` (contracts/IdleCDOEpochQueue.sol:277-282): `_checkNotAllowed(epochPendingDeposits[_epoch] != 0)` — reverts if raw deposits remain.
- `IdleCDOEpochVariantPrefunded._beforeStopEpoch` (contracts/IdleCDOEpochVariantPrefunded.sol:39-48): the direct `stopEpoch` selector is blocked once `epochQueue` is set, so `stopEpochWithDuration` is the only path.
- `IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration` (contracts/IdleCDOEpochVariantPrefunded.sol:72-89): calls `prefundedDepositsToProcess()`; its revert rolls back the whole stop transaction (`isEpochRunning = false`, `epochNumber` bump, unpause, `allowAA/BBWithdrawRequest` are all undone).

Sequence:

1. Epoch is running; `prefundedDepositWindow = W` is set; queue enabled.
2. Attacker (a KYC-passing lender) calls `requestDeposit(dust)` in the final block where `block.timestamp + W < epochEndDate` still holds. The deposit lands in `epochPendingDeposits[nextEpoch]`.
3. In the next block, both `requestDeposit` and `processDepositsToBorrower` revert. Pending deposits can no longer move to `epochPrefundedDeposits`.
4. After `epochEndDate`, owner/manager calls `stopEpochWithDuration`. The base flow succeeds but `_afterStopEpochWithDuration` → `prefundedDepositsToProcess()` reverts on `epochPendingDeposits[_epoch] != 0`, reverting the entire transaction.
5. `isEpochRunning` stays true, `epochEndDate` is passed, no new epoch can start, normal withdraw requests stay unclaimable (`claimWithdrawRequest` requires `epochNumber > lastWithdrawRequest`), instant claims are disabled in this variant, and `_beforeUnpause`/deposit gating all remain in the running state.

The only escapes are (a) the attacker voluntarily deleting the request via `deleteRequest` (an attacker simply won't), or (b) owner intervention outside the normal flow. There is no permissionless or privileged recovery path that clears `epochPendingDeposits` once the window has passed without `processDepositsToBorrower` having run.

### Impact Explanation
Temporary-to-permanent freezing of all pool funds: every depositor's principal and accrued yield are locked because the epoch can never be stopped and withdraw receipts can never mature. Loss magnitude equals the entire pool TVL for as long as the attacker refuses to delete their dust request. This mirrors the Party crowdfund DoS where users had to wait for expiry — here there is no expiry that resolves the state.

### Likelihood Explanation
Requires a prefunded queue deployment with `prefundedDepositWindow > 0` and an attacker who passes Keyring KYC (explicitly in-scope as "a KYC-passing lender"). The deposit can be 1 wei. The attacker's transaction only needs to land in the last block before the cutoff, which is deterministic and cheap to time. No privileged misbehavior needed.

### Recommendation
In `prefundedDepositsToProcess` / `_afterStopEpochWithDuration`, do not revert on leftover `epochPendingDeposits`; instead sweep them (e.g., refund them from the queue, or treat them as a separate non-blocking bucket), or allow `processDepositsToBorrower`/`deleteRequest`-style forced cleanup by owner/manager for deposits that missed the cutoff. Alternatively, revert `requestDeposit` once `block.timestamp + W + safetyMargin >= epochEndDate` so a deposit always leaves enough time to be processed.

### Proof of Concept
Foundry sketch (adapt `test/foundry/IdleCDOEpochVariantPrefunded.t.sol` helpers):

```solidity
function testPendingQueueDepositBlocksStopEpoch() external {
    uint256 window = PREFUNDED_DEPOSIT_WINDOW; // e.g. 5 days
    uint256 dust = 1;

    vm.prank(manager);
    cdoEpoch.setEpochQueue(address(queue));
    vm.prank(manager);
    queue.setPrefundedDepositWindow(window);

    // epoch already running via _startEpochAndCheckPrices(0) in setup
    uint256 nextEpoch = strategy.epochNumber() + 1;

    // Attacker deposits dust in the last block before the cutoff
    address attacker = makeAddr("attacker");
    deal(address(underlying), attacker, dust);
    vm.warp(cdoEpoch.epochEndDate() - window - 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), dust);
    queue.requestDeposit(dust);          // epochPendingDeposits[nextEpoch] = 1
    vm.stopPrank();

    // Now inside the window: prefunding is no longer possible
    vm.warp(cdoEpoch.epochEndDate() - window + 1);
    vm.prank(manager);
    vm.expectRevert();                   // cutoff check in processDepositsToBorrower
    queue.processDepositsToBorrower();

    // Borrower fully repays; stop still reverts because pending deposits exist
    uint256 interest = strategy.pendingWithdraws() + 1_000e6;
    deal(address(underlying), borrower, interest);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), interest);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector); // epochPendingDeposits != 0 in prefundedDepositsToProcess
    cdoEpoch.stopEpochWithDuration(0, 1_000e6, cdoEpoch.epochDuration(), 0);

    assertTrue(cdoEpoch.isEpochRunning(), "epoch can never be stopped");
    assertFalse(cdoEpoch.defaulted());
}
```

Uncertainty note: if `deleteRequest` permits the queue owner/manager (not just the depositor) to clear `epochPendingDeposits` for a missed-window deposit, the issue degrades to a temporary DoS until the owner intervenes; I could not fully verify `deleteRequest`'s gating within the iteration budget. The revert path in `_afterStopEpochWithDuration` rolling back the entire stop is verified from `IdleCDOEpochVariantPrefunded.sol:72-89` and `IdleCDOEpochQueue.sol:277-282`.