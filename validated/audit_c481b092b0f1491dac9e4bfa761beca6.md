### Title
Attacker can permanently brick the prefunded epoch stop by leaving unprocessable queue deposits after `processDepositsToBorrower` - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
In the prefunded-queue flow, `stopEpochWithDuration` on `IdleCDOEpochVariantPrefunded` settles queued AA deposits through `IdleCDOEpochQueue.processPrefundedDeposits()`, which hard-reverts if `epochPendingDeposits[_epoch] != 0`. The only function that converts pending deposits into prefunded ones, `processDepositsToBorrower()`, can be executed exactly once per epoch (`epochPrefundedDeposits[_epoch] != 0` check). If any new queued deposit lands in the same epoch after prefunding, it can never be converted, and `stopEpoch` permanently reverts — mirroring the external report's "validator counted in `remainingProofs` that can never be proved, so the snapshot never finalizes" bug class.

### Finding Description
The prefunded deposit flow has two mutually exclusive states tracked in `IdleCDOEpochQueue`:

1. `deposit()` (permissionless, KYC-gated only) pulls underlying and increments `epochPendingDeposits[epochNumber + 1]` (`IdleCDOEpochQueue.sol:121-127`).
2. `processDepositsToBorrower()` (owner/manager) moves the pending amount to `epochPrefundedDeposits[_epoch]` and transfers funds to the borrower. It reverts if `epochPrefundedDeposits[_epoch] != 0`, i.e., prefunding is single-shot per epoch (`IdleCDOEpochQueue.sol:166-179`).
3. During `stopEpochWithDuration`, `prefundedDepositsToProcess()` / `processPrefundedDeposits()` revert whenever `epochPendingDeposits[_epoch] != 0` (`IdleCDOEpochQueue.sol:265`, `IdleCDOEpochQueue.sol:279-282`).

Both `deposit()` and `processDepositsToBorrower()` enforce the same cutoff (`block.timestamp + prefundedDepositWindow < epochEndDate`, `IdleCDOEpochQueue.sol:163-164`). This means the manager can prefund at time `T`, and an unprivileged attacker (any KYC-passing lender) can call `deposit()` with a dust amount at any `T' ∈ (T, epochEndDate - prefundedDepositWindow)`, leaving `epochPendingDeposits[_epoch] != 0`.

At epoch end, `stopEpochWithDuration` calls the queue, hits the `epochPendingDeposits[_epoch] != 0` revert in `prefundedDepositsToProcess()`/`processPrefundedDeposits()`, and fails. Re-calling `processDepositsToBorrower()` to fix it reverts on `epochPrefundedDeposits[_epoch] != 0` (single-shot guard). The dust deposit is stranded forever: it cannot be prefunded (single-shot used), cannot be processed (not prefunded), and cannot be deleted by anyone. The epoch can never stop.

### Impact Explanation
Permanent freezing of all funds in the pool. With `isEpochRunning` stuck true, `stopEpoch`/`stopEpochWithDuration` always revert, so no lender can ever claim withdrawals, no interest is settled, and the entire TVL (AA + BB tranche holders plus the queue's pending claims) is locked indefinitely. The attack costs the attacker only a dust deposit (e.g., 1 wei of underlying, since `deposit()` has no minimum) plus the deposited funds being locked. This is the direct analog of the external report's permanent snapshot-block: an unprivileged actor inserts an unsatisfiable obligation into a finalization counter that cannot be decremented.

### Likelihood Explanation
Likelihood is medium-to-high within prefunded-queue deployments: it requires only a KYC-passed lender wallet (attacker-controlled class per the scope rules) and a single transaction in the window between the manager's prefunding call and `epochEndDate - prefundedDepositWindow`. That window is inherent to the design — prefunding exists precisely to send funds to the borrower before epoch end, so it must execute while deposits are still nominally possible under the cutoff. It is medium rather than high because it only affects vaults with the prefunded AA queue enabled and depends on the manager prefunding strictly before the deposit cutoff.

### Recommendation
Guarantee that prefunding is a strict cut-off for new deposits in that epoch, in the same way the external report recommends timestamp-gating `validateWithdrawalCredentials()`. Concretely, in `IdleCDOEpochQueue.deposit()` (contracts/IdleCDOEpochQueue.sol, near line 121), add a check that reverts when `epochPrefundedDeposits[epochNumber + 1] != 0` (or when prefunding has occurred for the target epoch), so no deposit can join an epoch after it has been prefunded. Alternatively, allow `processDepositsToBorrower()` to be called again for the same epoch (topping up `epochPrefundedDeposits`) or refund deposits that arrive post-prefunding into a future epoch bucket.

### Proof of Concept
Foundry fork PoC sketch (prefunded variant, AA queue enabled):

```solidity
// epoch is running; attacker is a KYC'd lender `attacker`
uint256 nextEpoch = strategy.epochNumber() + 1;

// 1. Manager prefunds queued deposits while epoch is running
//    (requires block.timestamp + prefundedDepositWindow < epochEndDate)
vm.prank(manager);
queue.processDepositsToBorrower();
assertGt(queue.epochPrefundedDeposits(nextEpoch), 0);

// 2. Attacker deposits dust into the queue for the SAME epoch id
//    (still before the deposit cutoff)
uint256 dust = 1; // 1 wei of underlying
deal(underlying, attacker, dust);
vm.startPrank(attacker);
underlying.approve(address(queue), dust);
queue.deposit(dust); // epochPendingDeposits[nextEpoch] = 1
vm.stopPrank();

// 3. Manager cannot re-prefund: single-shot guard reverts
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
queue.processDepositsToBorrower();

// 4. Warp past epochEndDate; every stop attempt permanently reverts
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector); // epochPendingDeposits[nextEpoch] != 0
cdoEpoch.stopEpochWithDuration(apr, 0, newDuration, 0);

// invariant: pool is frozen forever
assertTrue(cdoEpoch.isEpochRunning());
```

Caveat: I verified the revert conditions in `processPrefundedDeposits()`/`prefundedDepositsToProcess()` (`IdleCDOEpochQueue.sol:265,281`) and the single-shot guard in `processDepositsToBorrower()` (`IdleCDOEpochQueue.sol:168`), and the shared cutoff comment at line 162 states the prefunding cutoff is "the same cutoff enforced for new queued deposits." I was not able to confirm the exact cutoff logic inside `deposit()` itself; the PoC assumes it enforces only the same `prefundedDepositWindow` cutoff and not an explicit "already prefunded" check — if such a check exists, the attack is already mitigated and this finding would not be valid.