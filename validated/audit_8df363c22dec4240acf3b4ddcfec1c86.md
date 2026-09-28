I found the analog surface. Let me verify the gating and the revert path before writing up.### Title
Dust `requestDeposit` on the AA prefunded queue permanently bricks `stopEpochWithDuration`, freezing all vault funds - ([File: contracts/IdleCDOEpochQueue.sol](contracts/IdleCDOEpochQueue.sol))

### Summary
The Deskflow bug is a "one cheap bad input stalls the shared worker for everyone" class. The analog is the prefunded epoch-settlement path: `IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration` unconditionally calls `IdleCDOEpochQueue.prefundedDepositsToProcess()`, which reverts whenever `epochPendingDeposits[settleEpoch] != 0`. Any KYC-allowed wallet can leave a 1-wei raw deposit in the settle epoch, causing every subsequent `stopEpochWithDuration` to revert — the epoch never rolls and all pool funds freeze.

### Finding Description
In prefunded mode, `requestDeposit` writes raw underlyings into `epochPendingDeposits[epochNumber + 1]` and only blocks new deposits once that epoch has been prefunded (`epochPrefundedDeposits[nextEpoch] != 0`) or once inside `prefundedDepositWindow` (contracts/IdleCDOEpochQueue.sol:103-127). `processDepositsToBorrower` — the only privileged way to clear raw pending deposits by converting them into prefunded ones — is itself blocked inside the same cutoff window: `_checkNotAllowed(_prefundedWindow == 0 || block.timestamp + _prefundedWindow < _cdo.epochEndDate())` (contracts/IdleCDOEpochQueue.sol:163-164). `deleteRequest` can only be called by the depositor (contracts/IdleCDOEpochQueue.sol:184-199).

On settlement, `prefundedDepositsToProcess` reverts on any leftover raw pending amount — note it reverts even when `epochPrefundedDeposits[_epoch] == 0`, because the check runs unconditionally:

```solidity
// contracts/IdleCDOEpochQueue.sol:277-282
_prefunded = epochPrefundedDeposits[_epoch];
_checkNotAllowed(epochPendingDeposits[_epoch] != 0);
```

This call sits inside `_afterStopEpochWithDuration` (contracts/IdleCDOEpochVariantPrefunded.sol:72-88), so the revert bubbles through `stopEpochWithDuration` after borrower funds were conceptually pulled — atomically reverting the entire stop.

Attack sequence (running epoch N, prefunded AA queue enabled, `prefundedDepositWindow = W`):
1. Attacker (any KYC-passing wallet, in scope) calls `queue.requestDeposit(1 wei)` at `epochEndDate - W - 1` — the last block before the cutoff.
2. Manager cannot prefund: `processDepositsToBorrower` now reverts (`now + W >= epochEndDate`). Manager cannot delete: `deleteRequest` is caller-scoped.
3. At `epochEndDate`, manager calls `stopEpochWithDuration` → `_afterStopEpochWithDuration` → `prefundedDepositsToProcess` sees `epochPendingDeposits[N+1] = 1` → revert. Every retry reverts identically.

### Impact Explanation
Broken invariant: epoch state machine liveness. The vault can never exit epoch N — borrower principal and all tranche-holder funds are permanently frozen, since no privileged actor can remove the attacker's dust request and `stopEpoch` (direct selector) is also blocked when a queue is configured (`_beforeStopEpoch`, contracts/IdleCDOEpochVariantPrefunded.sol:43). Cost to attacker: 1 wei of underlying plus a KYC'd wallet. Loss: the entire TVL is locked until the attacker voluntarily calls `deleteRequest`, i.e. permanent freezing of all funds.

### Likelihood Explanation
Requires the prefunded variant with `epochQueue` configured and `prefundedDepositWindow != 0` — the exact configuration this code path was built for. The attacker needs only KYC clearance (an allowed "KYC-passing lender" per scope) and a gas-cheap transaction timed near the cutoff; even un-timed, any epoch where the manager simply doesn't prefund leaves queued dust that bricks settlement. One caveat I could not fully confirm in this pass: `_checkAllowed`'s exact conditions (whether deposits are additionally restricted to the running phase); if deposits are also permitted during the buffer period, the attack surface is wider, not narrower.

### Recommendation
In `prefundedDepositsToProcess`/`_afterStopEpochWithDuration`, do not revert on leftover `epochPendingDeposits`; instead return them to users in a claimable ledger (or auto-route them through the normal `depositAA` path during the stop). Alternatively, allow `processDepositsToBorrower` inside the window up to `epochEndDate`, or add a privileged sweep that force-refunds raw pending deposits for the settle epoch.

### Proof of Concept
Foundry fork PoC outline (fork prefunded-variant deployment, or `vm.etch` like `_useStandardEpochVariant` in test/foundry/IdleCDOEpochQueue.t.sol:1467):

```solidity
// setup: cdoEpoch is IdleCDOEpochVariantPrefunded, epochQueue set, prefundedDepositWindow = W
// 1. attacker deposits 1 wei just before the cutoff
vm.warp(cdoEpoch.epochEndDate() - W - 1);
deal(address(underlying), attacker, 1);
vm.startPrank(attacker);
underlying.approve(address(queue), 1);
queue.requestDeposit(1);
vm.stopPrank();

// 2. manager can no longer prefund
vm.warp(cdoEpoch.epochEndDate() - W);
vm.prank(manager);
vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
queue.processDepositsToBorrower();

// 3. every stopEpochWithDuration reverts via prefundedDepositsToProcess
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
cdoEpoch.stopEpochWithDuration(apr, 0, duration, 0);
```