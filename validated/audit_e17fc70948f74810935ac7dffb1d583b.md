### Title
Loss haircut stored under a single epoch key lets cross-epoch withdraw receipts claim at par — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` applies a pro-rata loss to the **aggregate** `pendingWithdraws` bucket but records the recovery ratio under a single epoch key (`lossRecoveryPriceByEpoch[epochNumber]`). Claims, however, look the ratio up through the user's `lastWithdrawRequest` epoch and clear only that epoch's slice in `withdrawsRequestsByEpoch`. Receipts whose request epoch differs from the stored loss epoch skip the haircut entirely and are paid at par through `_claimFundedWithdrawRequest`, even though the strategy only collected `pendingBasis * lossRecoveryPrice`. This is the direct analog of GHSA-55mm-5399-7r63: a value (the loss-adjusted recovery price) is not bound to the full context it was computed over (all pending receipts), so receipts "replay" in a context where they were never meant to be valid at full value.

### Finding Description
In `requestWithdraw` (lines 243–295) each request is recorded per user per epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]`, aggregated in `withdrawsRequests[_user]` and `pendingWithdraws`, while `lastWithdrawRequest[_user]` stores only the latest request epoch.

In `collectWithdrawFunds` (lines 411–430), when the borrower underfunds (`_amount < pendingBasis`), the haircut `lossRecoveryPrice = _amount * 1e18 / pendingBasis` is computed over the whole aggregate pending bucket, `pendingWithdraws` is zeroed, but the price is stored only under `lossRecoveryPriceByEpoch[epochNumber]`.

On claim, `_claimLossAdjustedWithdrawRequest` (lines 789–801) reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and `_clearWithdrawClaimForEpoch` (lines 811–837) zeroes only `withdrawsRequestsByEpoch[_user][_claimEpoch]`. Any receipt slice belonging to a different request epoch remains in `withdrawsRequests[_user]` and is then paid 1:1 by `_claimFundedWithdrawRequest` (lines 319–350).

Two concrete mismatch paths:

1. **Multi-epoch receipts, single loss key.** A user (or two users via the shared `epochNumber` key) holds unfunded receipts requested in different epochs when a loss is realized. `pendingWithdraws` summed all of them, so the funded amount assumes all take the haircut. But the price is only reachable through `lastWithdrawRequest`/`epochNumber`; slices recorded under other epochs are never cleared by `_clearWithdrawClaimForEpoch` and withdraw at par.
2. **Wrong-key lookup.** If `epochNumber` has already been incremented (it is bumped inside `deposit()` when `isEpochRunning`, line 610) relative to the request epoch of part of the pending bucket, even the user whose receipts were haircutted in aggregate sees `lossRecoveryPriceByEpoch[lastWithdrawRequest] == 0`, skips the loss path, and claims fully at par.

The guard at lines 263–271 only prevents *new* requests while an unclaimed loss-adjusted receipt exists at `lastWithdrawRequest`; it does nothing for receipts already aggregated under other epoch keys.

### Impact Explanation
The strategy collects `pendingBasis * price` underlying but pays out `claimBasis * price` for the keyed epoch **plus** `claimBasis * 1e18` for every other-epoch slice. The overpayment comes directly from strategy-held underlying that backs other pending receipts and active tranche holders, producing insolvency: early claimers are made whole at par while later claimers' funded claims are drained, or `claimWithdrawRequest` for remaining users reverts on insufficient balance. Quantified loss is `(1 - lossRecoveryPrice) * (non-keyed-epoch receipt basis)`, up to the full pending bucket minus the keyed slice.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`stopEpoch` loss while `pendingWithdraws` contains receipts recorded under a different epoch key than `epochNumber`. This is reachable whenever withdrawals accumulate across a buffer boundary before funding, or when a previously haircut user still holds old slices and new pending receipts are haircutted again — all actions available to ordinary KYC'd lenders calling `requestWithdraw`/`claimWithdrawRequest` through `IdleCDOEpochVariant`, with only honest manager/borrower sequencing needed for the loss event.

### Recommendation
Bind the loss price to every receipt it was computed over, mirroring the cookie-name binding fix:
- Either store per-user-per-epoch recovery factors at funding time, or record a global "pending receipts covered" watermark so `_claimLossAdjustedWithdrawRequest` applies `lossRecoveryPrice` to **all** slices in `withdrawsRequests[_user]`/`apr0Users[_user]` present when the loss was realized, not just the `lastWithdrawRequest` epoch.
- Alternatively, snapshot the request epoch set covered by each `lossRecoveryPriceByEpoch` entry and revert/haircut any claim whose basis predates it, so no slice can route to the par-funded path.

### Proof of Concept
Foundry fork sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testCrossEpochReceiptEvadesHaircut() external {
    // userA deposits AA and requests withdraw in epoch N-1
    uint256 reqA1 = cdoEpoch.requestWithdraw(aaBalUserA / 2, address(AAtranche));
    // epoch N-1 stops but only partially funds -> pendingWithdraws still carries reqA1
    // (or: reqA1 requested in the buffer of epoch N while an older slice still sits
    //  under withdrawsRequestsByEpoch[userA][N-1])
    _startEpochAndCheckPrices(N);

    // userA requests again in epoch N -> lastWithdrawRequest = N,
    // withdrawsRequestsByEpoch has slices under N-1 and N
    uint256 reqA2 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // borrower underfunds at stopEpoch: collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[epochNumber] over the WHOLE pendingBasis
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 funded = pendingBasis / 2; // 50% haircut
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(...lossParams...); // triggers collectWithdrawFunds(funded)

    // userA claims: only the epoch-`epochNumber` slice is haircutted;
    // the N-1 slice pays at par via _claimFundedWithdrawRequest
    uint256 balPre = underlying.balanceOf(userA);
    vm.prank(userA);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(userA) - balPre;

    // strategy only received `funded`; userA extracted > funded share
    assertGt(paid, funded * (reqA1 + reqA2) / pendingBasis);
    // subsequent claimants revert on insufficient underlying => insolvency
}
```

Exact epoch indexing should be confirmed against `deposit()`'s `epochNumber += 1` ordering (line 610); I did not fully trace whether `collectWithdrawFunds` executes before or after that bump within `stopEpoch`, but either ordering leaves a window where aggregate receipts and the single epoch key diverge.