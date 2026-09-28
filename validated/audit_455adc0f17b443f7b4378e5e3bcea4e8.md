### Title
Dust withdraw request permanently bricks `processWithdrawRequests`, freezing all queued withdrawals for the epoch - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
The Undertow advisory is a DoS where a malformed, attacker-crafted request poisons shared connection state. The analog in idle-tranches is `IdleCDOEpochQueue.processWithdrawRequests`: a single dust-sized withdraw request makes the computed `epochWithdrawPrice` round to zero, so the function reverts `Is0` on every call. Because `epochPendingWithdrawals[epoch]` can never be processed while the dust remains and only the dust depositor can delete their own request, all other users' queued withdrawals for that epoch are frozen at near-zero attacker cost.

### Finding Description
`requestWithdraw` accepts any `amount` (including dust) from any KYC-allowed wallet, transfers tranche tokens to the queue, and adds them to `epochPendingWithdrawals[nextEpoch]` with no minimum-amount check (`contracts/IdleCDOEpochQueue.sol:131-143`).

During the buffer period, `processWithdrawRequests` (owner/manager-called) computes:

```solidity
uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
if (_epochPrice == 0) { revert Is0(); }
```

(`contracts/IdleCDOEpochQueue.sol:315-321`). If `_pending` is small enough that the returned `_underlyingsRequested` rounds to 0 (a dust tranche request, as demonstrated by the existing test `testProcessWithdrawRequestsWith0Price` where a 100-wei tranche request triggers the `Is0` revert), the whole call reverts. The revert rolls back the CDO-level `requestWithdraw`, so no state changes — every subsequent call reverts identically.

Recovery paths do not help honest users:
- `deleteWithdrawRequest` is per-user (`userWithdrawalsEpochs[msg.sender]`); other users deleting their requests shrinks `_pending` but the attacker's dust remains, keeping `_epochPrice == 0`. Only the attacker can remove the blocking request (`contracts/IdleCDOEpochQueue.sol:203-218`).
- `isEpochWithdrawZero` can never be set because it is only written inside `processWithdrawalClaims`, which is unreachable while `processWithdrawRequests` reverts (`contracts/IdleCDOEpochQueue.sol:355-362`).
- `pendingClaims` stays false, so the manager cannot skip past it.

### Impact Explanation
Broken invariant: queued/delayed-withdrawal liveness ("one receipt → one payout path"). All tranche tokens queued by legitimate users for that epoch (`epochPendingWithdrawals[epoch]`, potentially the full pool's pending exits) are stuck in the queue contract: they cannot be processed into `pendingWithdraws` receipts, and users can only recover raw tranche tokens via `deleteWithdrawRequest`, losing their position in the withdrawal queue for the epoch. The attacker's cost is permanently forfeiting dust tranche tokens (well under 1 wei of underlying). This is a temporary-to-permanent freezing of user withdrawals, gated only on the attacker choosing not to delete — effectively permanent for any epoch where a griefer exists, repeatable every epoch.

### Likelihood Explanation
Requirements: a KYC-passing wallet holding ≥1 unit of tranche tokens and calling `queue.requestWithdraw(dust)` while an epoch is running. No privileged role, no capital, no timing race. Any MEV/griefing-motivated holder or a borrower-adjacent actor can execute it permissionlessly each epoch.

### Recommendation
Enforce a minimum in `queue.requestWithdraw`, e.g. require `amount` such that the implied underlying is non-zero (`_cdo.requestWithdraw` preview or a `MIN_WITHDRAW` floor), or skip/zero-out requests that would yield a zero `epochWithdrawPrice` by settling them via `isEpochWithdrawZero` instead of reverting. Alternatively, allow manager to force-delete dust requests that make `epochPendingWithdrawals` unprocessable.

### Proof of Concept
Fork test extending `test/foundry/IdleCDOEpochQueue.t.sol` setup (already confirmed by `testProcessWithdrawRequestsWith0Price` at lines 1431-1456):

```solidity
function testDustWithdrawRequestDoS() external {
    _stopCurrentEpoch();
    address victim = makeAddr('victim');
    address griefer = makeAddr('griefer');

    _depositWithUser(victim, 100e6);   // victim: 100 USDC
    _depositWithUser(griefer, 1e6);    // griefer: 1 USDC

    vm.prank(manager);
    cdoEpoch.startEpoch();             // epoch running

    _requestWithdrawWithUser(victim, tranche.balanceOf(victim));
    _requestWithdrawWithUser(griefer, 100); // dust: 100 wei tranche

    _stopCurrentEpoch();               // buffer period, epoch N+1

    // honest manager tries to settle queued withdrawals — always reverts
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    queue.processWithdrawRequests();

    // victim cannot claim (no epochWithdrawPrice set)
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(victim);
    queue.claimWithdrawRequest(strategy.epochNumber());

    // victim deletes own request — DoS persists, griefer dust still blocks
    vm.prank(victim);
    queue.deleteWithdrawRequest(strategy.epochNumber());
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    queue.processWithdrawRequests();
}
```

Victim recovers tranche tokens but loses the queued withdrawal for the epoch; the griefer's dust permanently blocks `processWithdrawRequests` for every other requester in that epoch unless the griefer deletes.