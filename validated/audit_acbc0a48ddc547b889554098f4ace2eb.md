### Title
Loss haircut applied to all pending receipts is only enforced on the user's latest request epoch, letting earlier pending receipts claim at par and drain the loss-adjusted reserve — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` haircuts the *entire* `pendingWithdraws` bucket when the borrower under-funds a stop (recorded as `lossRecoveryPriceByEpoch[epochNumber]`), but `_claimLossAdjustedWithdrawRequest` only looks up `lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch` only clears the per-epoch basis `withdrawsRequestsByEpoch[_user][lossEpoch]`. A user with pending receipts spanning two epochs can have the older receipt paid at par via `_claimFundedWithdrawRequest` even though the borrower only funded the haircutted aggregate.

### Finding Description
- `requestWithdraw` accumulates per-epoch basis and allows multiple pending receipts across epochs (comment at `IdleCreditVault.sol:323-324` explicitly notes a second request delays *both* receipts). The guard at `IdleCreditVault.sol:263-271` only blocks a new request when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` — i.e. only if the *immediately preceding* request epoch was loss-adjusted.
- At a lossy stop, `collectWithdrawFunds` (`IdleCreditVault.sol:411-430`) computes `lossRecoveryPrice = _amount * 1e18 / pendingBasis` against the *global* `pendingWithdraws`, sets `pendingWithdraws = 0`, and pulls only `_amount` underlying — which is less than the sum of all pending receipts.
- On claim, `_claimLossAdjustedWithdrawRequest` (`IdleCreditVault.sol:789-801`) uses `lossEpoch = lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch` (`IdleCreditVault.sol:811-837`) clears only `withdrawsRequestsByEpoch[_user][lossEpoch]`, subtracting only that epoch's amount from `withdrawsRequests[_user]` and resetting `lastWithdrawRequest[_user] = 0`.
- The residual `withdrawsRequests[_user]` (the older epoch's basis) then flows into `_claimFundedWithdrawRequest` (`IdleCreditVault.sol:319-350`), which pays it at par because `lastWithdrawRequest[_user]` was zeroed, bypassing the epoch-wait check and the haircut.

Concretely: user requests R1 in epoch N and R2 in epoch N+1 (allowed, since epoch N had no loss). Borrower under-funds the epoch N+1 stop, so `lossRecoveryPriceByEpoch[N+1] = p < 1` and the strategy receives `(R1+R2)*p`. The user claims `R2*p` through the loss-adjusted path, then `R1` in full through the funded path — total `R2*p + R1` > `(R1+R2)*p`.

### Impact Explanation
The invariant "one receipt, one haircutted payout" is broken: the loss waterfall applied to the whole pending bucket is only enforced on the latest per-epoch slice. The first claimant over-withdraws; the deficit is socialized onto other pending-receipt holders and the loss-adjusted reserve, causing insolvency for later claimers (their `safeTransfer` reverts or they receive less than `claimBasis * price`). With a 50% haircut and equal R1/R2, the attacker extracts an extra `R1*(1-p)` = up to ~25% of the funded reserve beyond their entitlement, scaled by how much older-epoch basis they stack.

### Likelihood Explanation
Requires an unprivileged lender who makes withdraw requests in two consecutive epochs before a `stopEpochWithDuration`/`stopEpoch` that realizes a loss (borrower under-funding pending withdrawals). Lossy stops are a designed code path (`previewLossAdjustedWithdrawFunds`), all attacker actions are ordinary `requestWithdraw`/`claimWithdrawRequest` calls via `IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest` (`IdleCDOEpochVariant.sol:751-791, 967-971`), and no existing guard (skim, `_onlyIdleCDO`, the `lossRecoveryPrice` pre-request check) covers the older epoch slice.

### Recommendation
Track loss-adjusted basis per user across *all* epochs, not just `lastWithdrawRequest`. Options: maintain a per-user set/checkpoint of pending epochs, or accumulate the claimable loss-adjusted basis into a single per-user field at `collectWithdrawFunds` time; alternatively, in `_claimLossAdjustedWithdrawRequest`, apply `lossRecoveryPrice` to the user's *entire* outstanding `withdrawsRequests[_user]`/APR0 basis for epochs `<= lossEpoch` rather than only `withdrawsRequestsByEpoch[_user][lossEpoch]`, since the haircut was computed on the global aggregate.

### Proof of Concept
Foundry fork PoC (against the epoch vault, e.g. extending `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testLossHaircutOnlyAppliesToLastEpoch() external {
    // deposit AA, run epoch 0, stop cleanly
    idleCDO.depositAA(100_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // epoch N request: R1 pending
    uint256 r1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    _startEpochAndCheckPrices(1);

    // epoch N+1 request: R2 pending (allowed: epoch N had no loss price)
    uint256 r2 = cdoEpoch.requestWithdraw(mintedAA / 4, address(AAtranche));
    _startEpochAndCheckPrices(2);

    // borrower under-funds: stopEpochWithDuration realizes a loss so that
    // only (r1+r2)*p is collected and lossRecoveryPriceByEpoch[2] = p < 1
    deal(defaultUnderlying, borrower, expectedFunds);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(apr, lossAmount); // triggers collectWithdrawFunds(short)

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    // pays r2 * p (loss-adjusted) + r1 * 1.0 (funded path) > (r1 + r2) * p
    uint256 paid = underlying.balanceOf(attacker) - balPre;
    assertGt(paid, (r1 + r2) * strategy.lossRecoveryPriceByEpoch(2) / 1e18);
}
```

The assertion demonstrates the over-claim: the attacker receives more than the haircutted entitlement, directly reducing what other receipt holders can claim from the same under-funded reserve.