### Title
Unprefunded queue dust deposit permanently bricks `stopEpochWithDuration` on the prefunded CDO variant - (contracts/IdleCDOEpochQueue.sol)

### Summary
On `IdleCDOEpochVariantPrefunded`, every successful epoch stop routes through `_afterStopEpochWithDuration`, which unconditionally calls `epochQueue.prefundedDepositsToProcess()` whenever `epochQueue != 0`. That view reverts if `epochPendingDeposits[_epoch] != 0`, i.e. if any raw queued deposit for the settled epoch was never forwarded to the borrower via `processDepositsToBorrower`. Once the prefunded deposit window closes, a leftover queued deposit can no longer be prefunded (line 164 requires `block.timestamp + prefundedDepositWindow < epochEndDate`), and there is no privileged path to clear `epochPendingDeposits` — only the depositor can remove it via `deleteRequest`. A KYC-passing attacker can therefore permanently block `stopEpochWithDuration` (and, after default, default finalization), freezing all pool funds.

### Finding Description
- `IdleCDOEpochVariantPrefunded._beforeStopEpoch` blocks the direct `stopEpoch` selector whenever a queue is set, so the manager must call `stopEpochWithDuration` (contracts/IdleCDOEpochVariantPrefunded.sol:39-48).
- `stopEpochWithDuration` → `_afterStopEpochWithDuration` → `IIdleCDOEpochQueuePrefunded(_queue).prefundedDepositsToProcess()` (lines 72-78).
- `prefundedDepositsToProcess` executes `_checkNotAllowed(epochPendingDeposits[_epoch] != 0)` — it reverts whenever the target epoch still holds queue-held deposits, regardless of whether anything was prefunded (contracts/IdleCDOEpochQueue.sol:277-282).
- Any KYC'd wallet can create such a deposit during the running epoch via `requestDeposit` (lines 103-127). The only AA-side restriction is the prefunded cutoff (`block.timestamp + prefundedDepositWindow >= epochEndDate` reverts) and `checkPrefunding`. If `prefundedDepositWindow == 0` there is no cutoff at all; even with a nonzero window the attacker simply deposits just before it lapses.
- After the cutoff, `processDepositsToBorrower` itself reverts (line 164), so the honest manager cannot sweep the residue into `epochPrefundedDeposits`. `epochPendingDeposits` can then only be decremented by the attacker calling `deleteRequest` (lines 184-199). The queue offers no privileged cleanup, and `setEpochQueue` documents the same invariant ("do not change the queue after deposits were already prefunded ... before stopEpochWithDuration settles") — swapping the queue while prefunded deposits exist would strand them anyway.
- The same revert fires on the default path: `_prefundedEpochToProcess` returns `epochNumber + 1` when `defaulted()`, which is exactly the epoch id user deposits were queued into during the running epoch (lines 288-291). A residue therefore also bricks `_handleBorrowerDefault`'s settlement path.

This mirrors the external bug class precisely: a counterparty that delivers "less than the expected settlement" (a queued deposit that never becomes prefunded) makes the stop flow hang indefinitely, denying service to every other lender.

### Impact Explanation
Permanent freezing of all lender funds in the vault. With a queue configured, no epoch stop path exists that bypasses `prefundedDepositsToProcess`, and there is no recovery mechanism: `restoreOperations`/`emergencyShutdown` do not clear queue state, and the deposit cannot be force-prefunded or removed by owner/manager. Until the attacker cooperates (or a rescue upgrade is shipped), all principal, pending withdrawal receipts, and prefunded borrower funds remain locked. Cost to the attacker is a dust deposit (no minimum enforced in `requestDeposit` beyond `checkPrefunding`'s guarded-launch cap) and KYC.

### Likelihood Explanation
Medium. The grief only requires a KYC-passing wallet and one well-timed `requestDeposit`. When `prefundedDepositWindow == 0` the attacker can deposit up to the final block of the epoch. When the window is nonzero, the attacker deposits immediately before the cutoff and relies on the manager not prefunding inside the window — operational, not attacker-controlled, but plausible since prefunding is discretionary. After default, any leftover pending deposit from the running epoch equally bricks settlement.

### Recommendation
- Let `prefundedDepositsToProcess` tolerate (or auto-refund) a residual `epochPendingDeposits` instead of reverting — e.g. have `processPrefundedDeposits` treat leftover queue-held deposits as part of the settled epoch or sweep them to depositors.
- Alternatively, extend the window cutoff logic so `requestDeposit` is closed strictly before `processDepositsToBorrower` becomes impossible (enforce `prefundedDepositWindow > 0` when the queue is enabled), and/or add an owner/manager escape to force `deleteRequest`-equivalent refunds for dust residues.

### Proof of Concept
Foundry fork PoC (modeled on `test/foundry/IdleCDOEpochQueue.t.sol` and `test/foundry/IdleCreditVault.t.sol` helpers `_stopCurrentEpoch`, `_depositWithUser`, `deal`, KYC'd `FASA` user):

```solidity
function testLeftoverQueueDepositBricksStopEpoch() external {
    // setup: prefunded IdleCDOEpochVariantPrefunded with epochQueue set (AA queue)
    _stopCurrentEpoch();                      // epoch 0 ends, buffer for epoch 1
    _depositWithUser(address(this), 100e6);   // normal AA liquidity
    vm.prank(manager);
    cdoEpoch.startEpoch();                    // epoch 1 running

    // attacker: KYC'd wallet deposits dust into the AA queue for nextEpoch = 2
    address griefer = makeAddr('griefer');    // whitelisted via Keyring in setup
    deal(defaultUnderlying, griefer, 1);
    vm.startPrank(griefer);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);                  // epochPendingDeposits[2] = 1 wei
    vm.stopPrank();

    // manager never prefunds the dust (window elapsed or simply skipped)
    vm.warp(cdoEpoch.epochEndDate() + 1);     // past cutoff: processDepositsToBorrower now reverts
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());

    // every stop attempt reverts inside prefundedDepositsToProcess
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, epochDuration);

    // emergency path is also bricked after borrower default
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, epochDuration); // defaulted()=true -> epoch epochNumber+1 still has pending

    // funds remain frozen until griefer voluntarily calls deleteRequest(2)
}
```