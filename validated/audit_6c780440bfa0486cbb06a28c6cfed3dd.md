### Title
Dust `requestWithdraw` in `IdleCDOEpochQueue` permanently DoSes `processWithdrawRequests` via zero-price `Is0` revert - ([File: contracts/IdleCDOEpochQueue.sol](contracts/IdleCDOEpochQueue.sol))

### Summary
`processWithdrawRequests` computes the epoch withdraw price as `underlyingsRequested * ONE_TRANCHE / pending` and reverts with `Is0()` when it rounds to zero. A KYC-passed attacker can queue a dust-sized withdrawal request (e.g. 1 wei of tranche tokens) whose underlying value rounds to 0, making the manager's `processWithdrawRequests` call always revert and freezing all withdrawals queued for that epoch.

### Finding Description
The external report concerns a resource parameter (gas limit) that is not validated against the work it must cover, causing failed execution at settle time. The direct analog here is a request amount that is not validated against the minimum needed for the settlement math to succeed.

In `contracts/IdleCDOEpochQueue.sol:315-320`:

```solidity
uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
if (_epochPrice == 0) {
  revert Is0();
}
```

`_pending` is the aggregate `epochPendingWithdrawals[_epoch]` filled by unprivileged `requestWithdraw(amount)` calls (`contracts/IdleCDOEpochQueue.sol:131-143`), gated only by `_checkAllowed` (epoch running + Keyring KYC).

Inside `IdleCDOEpochVariant.requestWithdraw` (`contracts/IdleCDOEpochVariant.sol:739-791`), a tranche amount of 1 wei produces `_underlyings = _trancheToUnderlyings(1) = 1 * _tranchePrice / 1e18`, which is 0 for any tranche priced below `1e18` underlyings per token (always true for USDC-denominated pools where price ≈ `1e6` per tranche unit). With `_underlyings == 0`, `interest`, `diff`, and `totalFees` are all 0, and `creditVault.requestWithdraw(0, ...)` returns early via `if (_amount == 0) return` (`contracts/strategies/idle/IdleCreditVault.sol:246`), so the CDO call returns `_underlyings = 0`.

Back in the queue: `_epochPrice = 0 * ONE_TRANCHE / _pending = 0` → `revert Is0()`. Because the revert happens before `epochPendingWithdrawals[_epoch]` is cleared, the attacker's dust request remains, so every subsequent `processWithdrawRequests` call reverts identically. There is no manager path to exclude a single requester; the epoch's queued withdrawals can never be processed while the dust entry exists.

### Impact Explanation
All tranche tokens queued for withdrawal in the affected epoch are frozen: `processWithdrawRequests` cannot execute, so `epochPendingClaims` is never set, `claimWithdrawRequest` always reverts `NotAllowed`, and affected users' only recourse is `deleteWithdrawRequest` to recover their tranche tokens and re-queue — which the attacker can re-grief in the next epoch for ~1 wei of tranche tokens each time. This is temporary freezing of user funds, repeatable at negligible cost, and during the frozen window users cannot exit even at par.

### Likelihood Explanation
Requirements are minimal: a wallet that passes Keyring KYC and a dust balance of the tranche token (attacker can deposit the minimum, then withdraw the rest). The manager call sequence (stop epoch → `processWithdrawRequests`) is routine, so the grief triggers deterministically. The only mitigation is that victims can individually `deleteWithdrawRequest`, which degrades the finding from permanent theft to repeated temporary freezing, matching the medium severity of the source report.

### Recommendation
In `IdleCDOEpochQueue.requestWithdraw`, enforce a minimum request size, e.g. convert `amount` to underlyings and require the implied payout to be non-zero (`_checkNotAllowed(amount * _cdo.virtualPrice(tranche) / ONE_TRANCHE == 0)`). Alternatively, in `processWithdrawRequests`, skip or return dust requests instead of reverting the whole batch, so a single sub-precision request cannot block the epoch.

### Proof of Concept
Foundry-style PoC against `IdleCDOEpochQueue` (same setup as `test/foundry/IdleCDOEpochQueue.t.sol::testProcessWithdrawRequests`):

```solidity
function testDustRequestBlocksProcessWithdrawRequests() external {
    _stopCurrentEpoch();              // buffer of epoch #1

    // honest user deposits and requests a normal withdrawal
    address user = makeAddr('user');
    uint256 tranches = _depositWithUser(user, 100e6);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _requestWithdrawWithUser(user, tranches);

    // attacker (KYC'd) deposits minimum, then queues a dust withdrawal
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1e6);           // 1 USDC
    // attacker keeps only 1 wei of tranche exposure queued
    vm.prank(attacker);
    queue.requestWithdraw(1);                  // 1 wei tranche -> 0 underlyings

    _stopCurrentEpoch();                       // buffer of epoch #2

    // manager tries to process all queued withdrawals -> always reverts
    vm.prank(manager);
    vm.expectRevert(Is0.selector);
    queue.processWithdrawRequests();

    // second attempt still reverts; honest user's withdrawal is frozen
    vm.prank(manager);
    vm.expectRevert(Is0.selector);
    queue.processWithdrawRequests();

    // honest user cannot claim nor self-rescue via processWithdrawRequests;
    // only deleteWithdrawRequest recovers their tranche tokens
    vm.prank(user);
    queue.deleteWithdrawRequest(strategy.epochNumber() + 1);
    // attacker's 1-wei dust still blocks the epoch permanently
}
```

Precondition used by the PoC: AA tranche `virtualPrice * 1 < ONE_TRANCHE` (1 wei of tranche is worth less than 1 wei of underlying), which holds for USDC pools. Key uncertainty I could not fully verify within iteration limits: whether `deleteWithdrawRequest` remains callable for victims in every mode (e.g., after default flags change), and whether this dust-revert path was previously acknowledged in audit docs; the mechanic itself is directly traceable in the code cited above.