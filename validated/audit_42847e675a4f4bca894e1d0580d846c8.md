### Title
Dust withdraw request permanently reverts `processWithdrawRequests`, freezing all queued withdrawals for the epoch - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
A crafted minimal input (a 1-wei tranche withdraw request) causes `IdleCDOEpochQueue.processWithdrawRequests` to revert on `Is0()` every time it is called for that epoch, because `IdleCDOEpochVariant.requestWithdraw` returns `0` underlyings for sub-dust tranche amounts while `epochPendingWithdrawals` remains non-zero. This mirrors CVE-2016-9633 (crafted input → resource-consumption DoS): here the "crafted page" is a dust request that makes the epoch-processing step unexecutable, blocking every honest user's queued withdrawal from being processed.

### Finding Description
In `contracts/IdleCDOEpochQueue.sol`, any KYC-passing wallet can call `requestWithdraw(amount)` with an arbitrarily small `amount`; the tranche tokens are pulled and `epochPendingWithdrawals[nextEpoch]` is incremented (lines 131-143). During the buffer, the manager calls `processWithdrawRequests`, which forwards the aggregated `_pending` tranche amount to `IdleCDOEpochVariant.requestWithdraw` and then computes:

```solidity
uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
if (_epochPrice == 0) { revert Is0(); }
```

(lines 315-321). In `IdleCDOEpochVariant.requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:739-791`), `_underlyings = _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN`. For `_amount` smaller than `ONE_TRANCHE_TOKEN / price` (e.g. 1 wei of tranche), this rounds to `0`. With `principal == 0`, interest and `_totalWithdrawFees` are also `0`, `IdleCreditVault.requestWithdraw(0, ...)` early-returns (`IdleCreditVault.sol:246`), and `_withdrawOps` burns the dust tranche for `0` underlyings — so the call returns `0` and the queue reverts at `Is0()`.

Because the revert happens *before* `epochPendingWithdrawals[_epoch]` is cleared, the pending amount stays non-zero forever. Every subsequent `processWithdrawRequests` call hits the same path and reverts again — an effectively infinite failure loop identical in spirit to the w3m infinite loop. The attacker is the only one who can remove the poison (`deleteWithdrawRequest` is per-`msg.sender`, lines 203-218) and can repeat the attack every epoch for a cost of ~1 wei of tranche tokens plus gas.

### Impact Explanation
While the dust request persists, no withdrawal request in that epoch can be processed: `epochWithdrawPrice` is never set, `pendingClaims` is never opened, and `claimWithdrawRequest` reverts for all users queued in that epoch (`epochPrice`/`epochWithdrawPrice` gating at lines 375-379 and the claim path). Honest users can individually self-rescue their tranche tokens via `deleteWithdrawRequest`, but their withdrawal intent is cancelled and must be re-queued for a later epoch — a forced, repeatable delay of withdrawals (temporary freezing of funds) attributable to a single unprivileged griefer. If the attacker sandwiches each epoch, queued withdrawals are denied every epoch.

### Likelihood Explanation
Requires only a KYC-passing wallet and a dust amount of tranche tokens; no privileged role, no borrower misbehavior, and no market condition. The only mitigation is that users can cancel their own requests, so the damage is denial/delay of service rather than permanent loss — consistent with Medium severity of the mapped CVE.

### Recommendation
In `processWithdrawRequests`, handle the zero-underlyings case instead of reverting: if `_underlyingsRequested == 0`, record the epoch as zero-payout (e.g. set `isEpochWithdrawZero[_epoch] = true`), clear `epochPendingWithdrawals[_epoch]`, and burn/return the dust tranche, rather than reverting with `Is0()`. Alternatively, enforce a minimum `amount` in `requestWithdraw`/`IdleCDOEpochVariant.requestWithdraw` so a request always converts to at least 1 underlying unit.

### Proof of Concept
Foundry fork test sketch (against the existing `IdleCDOEpochQueue` test harness in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function testDustWithdrawRequestBlocksEpoch() external {
    // FASA is KYC'd and holds tranche tokens; attacker deposits minimal and queues 1 wei
    _depositWithUser(ATTACKER, 1e6, true); // mint some tranche for attacker
    _requestWithdrawWithUser(FASA, ONE_TRANCHE); // honest user queues normal withdraw

    vm.prank(ATTACKER);
    queue.requestWithdraw(1); // dust request, same nextEpoch bucket

    _stopCurrentEpochWithApr(10e18);

    // manager processing reverts forever: requestWithdraw(1 wei) -> 0 underlyings -> Is0()
    vm.expectRevert(Is0.selector);
    vm.prank(manager);
    queue.processWithdrawRequests();

    // retry after time / new calls still revert; epochPendingWithdrawals stays non-zero
    vm.expectRevert(Is0.selector);
    vm.prank(manager);
    queue.processWithdrawRequests();

    // honest user cannot claim; only option is self-cancel via deleteWithdrawRequest
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(FASA);
    queue.claimWithdrawRequest(strategy.epochNumber());
}
```