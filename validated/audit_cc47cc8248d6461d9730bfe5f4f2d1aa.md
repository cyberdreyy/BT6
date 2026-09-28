### Title
Loss-adjusted withdraw receipt bypasses haircut and pays at par when the user makes a later withdraw request - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Lean kernel flaw — a value is checked against a declared tag (the projection's structure name) rather than its real type — `IdleCreditVault.claimWithdrawRequest` decides whether a receipt is loss-adjusted by looking only at `lastWithdrawRequest[_user]`, the *latest* request epoch. A lender whose receipt was haircut in epoch N via `stopEpochWithDuration(_lossAmount)` can register a new withdraw request in epoch N+1, which overwrites `lastWithdrawRequest`. The loss-adjusted receipt for epoch N is then never routed through `_claimLossAdjustedWithdrawRequest` (keyed to the wrong epoch), stays inside the aggregate `withdrawsRequests[_user]`, and is paid out at full par by `_claimFundedWithdrawRequest` — even though the borrower only funded `claimBasis * lossRecoveryPrice / RECOVERY_FULL` for it.

### Finding Description
In `requestWithdraw`, `lastWithdrawRequest[_user] = currentEpoch` is set on every request, and `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][currentEpoch]` accumulate across epochs (IdleCreditVault.sol:282-294).

`claimWithdrawRequest` (IdleCreditVault.sol:301-314) dispatches in order: post-default, defaulted-epoch, loss-adjusted, then funded.

`_claimLossAdjustedWithdrawRequest` (IdleCreditVault.sol:789-801) derives the loss epoch exclusively from `lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]`. If the user's most recent request is in a different epoch than the one that suffered the loss, the lookup returns 0 and the function returns without clearing the loss-epoch receipt. `_clearWithdrawClaimForEpoch` (the only place that removes an epoch's basis from the `withdrawsRequests` aggregate, IdleCreditVault.sol:811-837) is therefore never invoked for the loss epoch.

`_claimFundedWithdrawRequest` (IdleCreditVault.sol:319-350) gates only on `epochNumber <= lastWithdrawRequest[_user]` and then pays the entire aggregate `withdrawsRequests[_user]` at par via `_transferFundedClaim`, burning the receipt tokens 1:1.

Attack sequence (running epochs, loss-adjusted mode):

1. Lender deposits and calls `cdoEpoch.requestWithdraw(amount, tranche)` in epoch N.
2. Honest manager calls `stopEpochWithDuration(apr, 0, duration, lossAmount)`; the borrower funds only the loss-adjusted amount, and `lossRecoveryPriceByEpoch[N]` is set. The lender's basis for epoch N remains in `withdrawsRequests` / `withdrawsRequestsByEpoch`.
3. Attacker calls `requestWithdraw` again during epoch N+1's buffer → `lastWithdrawRequest[attacker] = N+1`.
4. After the next `stopEpoch`, `epochNumber > N+1`. `claimWithdrawRequest` runs: `_claimLossAdjustedWithdrawRequest` checks `lossRecoveryPriceByEpoch[N+1] == 0` → skipped; the funded path pays `withdrawsRequests[attacker]` — which still includes the full unhaircut epoch-N basis — at par.

Existing guards do not stop it: the epoch-wait check passes (N+1 ended), `_settleApr0` is unrelated, and no code path reconciles `withdrawsRequestsByEpoch` against `lossRecoveryPriceByEpoch` for epochs other than `lastWithdrawRequest`. The code comments even acknowledge the aggregation assumption: requesting again "will have to wait for another epoch to claim both requests" at par.

### Impact Explanation
The attacker receives `basis * lossRecoveryPrice / RECOVERY_FULL` more underlying than was funded for their epoch-N receipt. That excess is paid from the strategy's underlying balance reserved for other users' funded claims (`_transferFundedClaim`), i.e. direct theft followed by insolvency: the last claimants' `claimWithdrawRequest` calls revert on insufficient balance, permanently freezing their funded receipts. The excess equals `claimBasis_N * (1 - lossRecoveryPrice/RECOVERY_FULL)`; for a large-loss epoch this approaches the entire receipt size.

### Likelihood Explanation
Requires a `stopEpochWithDuration` epoch with `_lossAmount > 0` (a real loss event initiated by the honest manager — normal protocol operation, not attacker-controlled), plus one extra `requestWithdraw` by the attacker in a later epoch. Any KYC-passing lender or tranche holder can execute it with no privileged role. The preconditions are specific but plausible on a live credit vault that ever applies a partial loss.

### Recommendation
Track loss-adjusted claims per epoch rather than via a single `lastWithdrawRequest` marker. Options:

- In `_claimLossAdjustedWithdrawRequest`, iterate or record the set of epochs with nonzero `lossRecoveryPriceByEpoch` for which the user has `withdrawsRequestsByEpoch` entries (e.g. store a per-user list of request epochs), and clear each before falling through to the funded claim; or
- In `requestWithdraw`, refuse (or immediately settle) when the user has an earlier epoch receipt whose `lossRecoveryPriceByEpoch` is nonzero and unclaimed; or
- Subtract the haircut inside `withdrawsRequestsByEpoch`/`withdrawsRequests` at `stopEpochWithDuration` funding time so the aggregate never exceeds what was actually funded.

### Proof of Concept
Foundry fork sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossAdjustedReceiptClaimedAtPar() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());

    uint256 amount = 10_000 * ONE_SCALE;
    address victim = makeAddr('victim');
    _depositWithUser(victim, amount, true);          // victim, honest
    uint256 mintedAA = idleCDO.depositAA(amount);    // attacker = this contract

    _startEpochAndCheckPrices(0);

    // 1. Attacker requests withdraw in epoch N
    uint256 basisN = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // 2. Epoch ends with a real loss: stopEpochWithDuration(apr,0,duration,loss)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    uint256 loss = pending / 2; // 50% loss on pending receipts
    (uint256 pendingToFund,) = IdleCreditVault(address(strategy))
        .previewLossAdjustedWithdrawFunds(loss);
    deal(defaultUnderlying, borrower,
         cdoEpoch.expectedEpochInterest() + pendingToFund);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0,
                                   cdoEpoch.epochDuration(), loss);

    // 3. Attacker requests again in epoch N+1 -> lastWithdrawRequest moves past N
    cdoEpoch.requestWithdraw(mintedAA / 4, address(AAtranche));

    // victim also requests (funds epoch N+1 normally)
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // 4. Attacker claims: loss-adjusted path checks epoch N+1 (price 0) -> skipped;
    //    funded path pays aggregate withdrawsRequests (incl. epoch-N basis) at par.
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(address(this)) - balPre;

    uint256 lossPrice = IdleCreditVault(address(strategy))
        .lossRecoveryPriceByEpoch( /* epoch N */ 1 );
    uint256 funded = basisN * lossPrice / 1e18; // what borrower actually funded for epoch N
    // Attacker received full basisN instead of `funded`
    assertGt(got, funded, "loss-adjusted receipt paid at par");
}
```

Expected result: the attacker's payout exceeds the loss-adjusted funded amount by `basisN * (1 - lossRecoveryPrice/RECOVERY_FULL)`, draining underlying earmarked for the victim's funded claim; the victim's subsequent `claimWithdrawRequest` then reverts or pays short, demonstrating theft plus insolvency. (Exact epoch indices depend on the fork fixture; `lossRecoveryPriceByEpoch` and `previewLossAdjustedWithdrawFunds` are exercised in `test/foundry/IdleCDOEpochQueue.t.sol:817-835` and can be mirrored for `IdleCreditVault`.)