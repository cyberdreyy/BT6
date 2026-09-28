### Title
Queued epoch withdrawals misclassified as fully instant when `requestWithdraw` splits receipts — instant-vs-normal discriminator ignored, stranding the normal leg - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
`IdleCDOEpochQueue.processWithdrawRequests` classifies an entire epoch's aggregated withdrawal as "instant" based only on whether the queue's aggregate `instantWithdrawsRequests` balance increased during the single `_cdo.requestWithdraw(_pending, tranche)` call. This mirrors the kernel bug: a lookup/classification that ignores a discriminating field (A-TCAM vs C-TCAM ≈ normal vs instant receipt). When the CDO routes only part of the pending amount to instant receipts and the rest to normal withdraw receipts, `isEpochInstant[_epoch]` is set to `true`, and `processWithdrawalClaims` then calls only `claimInstantWithdrawRequest()`, which pays solely the instant bucket. The normal leg's underlyings are never pulled into the queue, yet `epochPendingClaims[_epoch]` is cleared and `epochWithdrawPrice[_epoch]` is rebased down by `_received / _pending`, permanently underpaying every user of that epoch.

### Finding Description
In `contracts/IdleCDOEpochQueue.sol`:

- Line 315–316: `_cdo.requestWithdraw(_pending, tranche)` is followed by `isEpochInstant[_epoch] = _strategy.instantWithdrawsRequests(address(this)) > _instantWithdraws;` — a boolean per epoch derived from the aggregate instant balance delta, with no record of how much went to the instant bucket versus the normal `withdrawsRequests` bucket.
- Line 326: `epochPendingClaims[_epoch] = _underlyingsRequested` records the full underlying amount for both legs.
- Lines 347–351: `processWithdrawalClaims` branches on `isEpochInstant[_epoch]` and calls either `claimInstantWithdrawRequest()` or `claimWithdrawRequest()` — never both.

In `contracts/strategies/idle/IdleCreditVault.sol`:

- `claimInstantWithdrawRequest` (lines 380–393) burns and pays only `instantWithdrawsRequests[_user]`, leaving `withdrawsRequests[_user]` untouched.
- Lines 355–357 of the queue then rebase `epochWithdrawPrice[_epoch]` by `_received / _pending`, so user claims via `claimWithdrawRequest` (lines 393–411) pay `amount * reducedPrice`, i.e. only the instant-funded fraction.

After the call, `epochPendingClaims[_epoch] = 0` and `pendingClaims = false`, so no later invocation can claim the stranded normal receipt — `processWithdrawalClaims` early-returns on `_pending == 0` (line 340). The normal leg remains as `withdrawsRequests[queue]` inside the strategy, claimable only by the CDO, with no queue path to reach it.

### Impact Explanation
Broken invariant: one receipt one payout / fair redemption. For any epoch where queued withdrawals are partially instant-eligible (mixed routing when instant liquidity covers only a subset, or when the min-APR-delta gate instant-fills some requests), all users of that epoch receive only `instantFunded / totalRequested` of their entitlement. The remainder is permanently frozen in `IdleCreditVault` as an orphaned `withdrawsRequests[queue]` receipt — no queue or user call can recover it. Loss = `normalLegAmount`, up to the full epoch withdrawal amount when the instant leg is small.

### Likelihood Explanation
Requires a running epoch with instant withdrawals enabled (`setInstantWithdrawParams` with a delay, per tests around `testProcessWithdrawalClaimsInstantEpoch`), queued withdrawals via `IdleCDOEpochQueue`, and a `requestWithdraw` that produces both instant and normal receipts in one call — i.e., partial instant liquidity. Owner/manager calls (`processWithdrawRequests`, `processWithdrawalClaims`) are honest and merely process whatever mix exists; an unprivileged attacker only needs to be a queue depositor whose epoch gets mixed-classified, or can even engineer the mix by sizing their own instant-eligible request so the CDO splits the queue's aggregate. No privileged misbehavior required.

Caveat: I could not fully trace `IdleCDOEpochVariant.requestWithdraw`'s split logic in the available context, so the precise conditions under which one call yields both receipt types should be confirmed in the PoC; the test at `test/foundry/IdleCDOEpochQueue.t.sol:1171-1179` ("now the available withdraws are only the normal ones") indicates mixed instant/normal handling exists across calls.

### Recommendation
Track the instant and normal legs separately instead of a boolean: store `epochInstantClaims[_epoch]` and `epochNormalClaims[_epoch]` from the deltas of `instantWithdrawsRequests` and `withdrawsRequests`, and in `processWithdrawalClaims` invoke `claimInstantWithdrawRequest()` and/or `claimWithdrawRequest()` as needed, summing both received amounts before computing `epochWithdrawPrice`. Alternatively, call both claim functions unconditionally (each is a no-op on an empty bucket).

### Proof of Concept
```solidity
// test/foundry/IdleCDOEpochQueueMixedClaim.t.sol
function testMixedInstantNormalEpochStrandsFunds() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);              // now in epoch #1
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // Two users deposit and queue withdrawals
    address u1 = makeAddr('u1'); address u2 = makeAddr('u2');
    uint256 t1 = _depositWithUser(u1, 1e6);
    uint256 t2 = _depositWithUser(u2, 100e6);
    vm.prank(manager); cdoEpoch.startEpoch();
    _requestWithdrawWithUser(u1, t1);
    _requestWithdrawWithUser(u2, t2);

    // stopEpoch such that only part of liquidity is instant-eligible,
    // so requestWithdraw creates BOTH instant and normal receipts for queue
    _stopCurrentEpochWithApr(1e18);
    uint256 epoch = strategy.epochNumber();

    vm.prank(manager);
    queue.processWithdrawRequests();
    // Both legs exist on the strategy, but the epoch is flagged instant
    assertGt(strategy.instantWithdrawsRequests(address(queue)), 0);
    assertGt(strategy.withdrawsRequests(address(queue)), 0);
    assertTrue(queue.isEpochInstant(epoch));            // misclassification

    vm.prank(manager); cdoEpoch.startEpoch();
    skip(101);
    queue.processWithdrawalClaims(epoch);               // claims ONLY instant leg

    // Normal leg is stranded: pending claims cleared, users underpaid
    uint256 expected = t1 * queue.epochWithdrawPrice(epoch) / 1e18;
    vm.prank(u1); queue.claimWithdrawRequest(epoch);
    // u1 received only instantLegShare < original entitlement
    assertGt(strategy.withdrawsRequests(address(queue)), 0); // orphaned receipt
    assertLt(underlying.balanceOf(u1), /*full priced amount*/ expected);
}
```