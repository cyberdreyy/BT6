### Title
Loss haircut applied only to the user's last request epoch lets earlier pending receipts claim at par, draining funds owed to other claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` computes a single `lossRecoveryPrice` over the aggregate `pendingWithdraws`, which can include receipts a user accumulated across multiple request epochs. However `_claimLossAdjustedWithdrawRequest` applies the haircut only to the segment stored under `lastWithdrawRequest[_user]`; the remaining per-epoch segments fall through to `_claimFundedWithdrawRequest` and are paid at 100% par. A user with pending receipts in two epochs can therefore recover more than their pro-rata share of the funded amount, leaving later claimants undercollateralized — the same failure shape as the hfi1 multi-iovec bug, where only the tail segment was treated as partial and earlier segments were consumed at full length.

### Finding Description
When a borrower shortfall occurs, `stopEpochWithDuration`/`stopEpoch` calls `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`. The vault zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` (lines 411-430). `pendingBasis` is the whole aggregate, so every pending receipt — regardless of which epoch it was requested in — is implicitly haircut to `lossRecoveryPrice`.

`requestWithdraw` intentionally lets a user stack requests across epochs without claiming (the "NOTE" at line 323-324: requesting again forces a longer wait, but `withdrawsRequestsByEpoch[_user][N]` and `withdrawsRequestsByEpoch[_user][M]` for `N < M` coexist, and both contribute to `pendingWithdraws`).

On claim, `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest` (line 312), which uses `lossEpoch = lastWithdrawRequest[_user]` — i.e., only the *most recent* request epoch M — and pays `withdrawsRequestsByEpoch[user][M] * lossRecoveryPrice`. It clears only that epoch's slice from `withdrawsRequests`. Then `_claimFundedWithdrawRequest` runs: `epochNumber > lastWithdrawRequest` (now reset to 0), so it pays the *remaining* `withdrawsRequests[_user]` — the epoch-N receipt — at full par (lines 338-349).

The invariant broken is loss socialization / pro-rata haircut: total payout = `M_amount * price + N_amount`, while the vault only received `(N_amount + M_amount) * price`. The overpayment `N_amount * (1 - price)` is pulled from funded underlyings reserved for other users' receipts.

### Impact Explanation
Direct theft of other claimants' funded withdrawal amounts. Any KYC'd tranche holder (unprivileged attacker) who happens to hold pending receipts spanning two request epochs extracts the un-haircut portion of the earlier epoch from the strategy's funded balance. Later claimants' `_transferFundedClaim` calls then revert on insufficient balance (permanent freezing of their claims) or pay out of reserve-adjacent funds. Loss magnitude scales with the earlier-epoch receipt size and `1 - lossRecoveryPrice`, which the attacker controls by sizing the first request and by the manager-realized loss.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`stopEpoch` shortfall (borrower repays less than `pendingWithdraws`), which is an ordinary operating path, not an edge case — `previewLossAdjustedWithdrawFunds` explicitly assigns part of any loss to pending receipts. The attacker only needs to make withdraw requests in two different epochs before a lossy stop, with no privileged role. No existing guard stops it: `requestWithdraw`'s loss-epoch check (lines 263-271) only inspects `lastWithdrawRequest`'s epoch and `apr0Users.principalEpoch`, so it never sees older per-epoch segments; `_withdrawClaimAmountsForEpoch` likewise scopes to a single epoch.

### Recommendation
Apply the epoch's `lossRecoveryPrice` to *all* of a user's pending receipts funded in that stop, not only the slice under `lastWithdrawRequest`. Concretely: either (a) record the loss haircut against every per-epoch bucket present in `pendingBasis` (e.g., track per-user list of request epochs, or store the loss price globally for the stop and apply it to the full `withdrawsRequests[_user]` balance cleared at claim time), or (b) change `collectWithdrawFunds` accounting so the haircut price is only computed over receipts belonging to the current epoch and older receipts are excluded from `pendingBasis` — matching the per-epoch clearing logic. Add a regression test where one user holds receipts in epochs N and M and a partial funding occurs at M's stop.

### Proof of Concept
Foundry fork test skeleton (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testLossHaircutOnlyAppliesToLastRequestEpoch() external {
    uint256 amountWei = 10000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    _transferBurnedTrancheTokens(address(this), true);

    // epoch 0: request withdraw #1 (epoch N segment)
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    uint256 req1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // epoch 1: request withdraw #2 without claiming (epoch M segment)
    _startEpochAndCheckPrices(1);
    uint256 req2 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // lossy stopEpoch: borrower funds only half of pendingWithdraws
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws(); // req1 + req2
    uint256 funded = pending / 2;
    deal(defaultUnderlying, borrower, funded);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(_lossForShortfall, funded); // sets lossRecoveryPrice ~0.5e18

    // claim: req2 is haircut to ~50%, req1 pays at par
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = underlying.balanceOf(address(this)) - balPre;

    // vault only received `funded` = (req1+req2)/2 but pays req1 + req2*0.5 > funded
    assertGt(claimed, funded, "claim exceeds funded amount; other claimants undercollateralized");
}
```

The assertion demonstrates the overpayment: the user's payout exceeds the aggregate amount the borrower funded for all pending receipts, so a subsequent claimant's `_transferFundedClaim` underflows/reverts on the drained balance.