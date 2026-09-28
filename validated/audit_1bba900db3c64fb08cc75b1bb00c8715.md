### Title
Loss-adjusted withdraw receipts can be re-papered at par by making a new withdraw request in a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keys the loss-adjusted claim path on `lastWithdrawRequest[_user]`, which stores only the *latest* request epoch. A user whose pending receipt was haircut in a `stopEpochWithDuration` loss epoch can simply submit a new withdraw request in a later epoch, which overwrites `lastWithdrawRequest`. The loss-adjusted claim path then looks up `lossRecoveryPriceByEpoch` for the wrong (later) epoch, finds zero, and returns — while the haircut receipt basis still sits inside the aggregate `withdrawsRequests[_user]` and is paid out at 100% par by `_claimFundedWithdrawRequest`. The contract only funded the haircut portion, so the excess is drained from reserves backing other users' claims.

### Finding Description
The kernel bug class is "teardown races ahead of an in-flight consumer": a marker that serializes access to a shared resource is overwritten, so the cleanup path frees/uses stale state. Here the shared resource is the claim basis `withdrawsRequestsByEpoch[_user][L]` plus the aggregate `withdrawsRequests[_user]`, and the serializing marker is `lastWithdrawRequest[_user]`.

Flow:

1. In `requestWithdraw`, the per-epoch and aggregate ledgers are incremented and the marker is unconditionally overwritten: `withdrawsRequests[_user] += _amount`, `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, `lastWithdrawRequest[_user] = currentEpoch` (lines 282–294). Nothing prevents a second request while a prior receipt is unclaimed — the code comments explicitly anticipate stacking ("if a user does not claim a withdraw request and instead requests another withdraw... he will have to wait for another epoch to claim both requests").

2. On a loss stop, `collectWithdrawFunds` zeroes `pendingWithdraws`, collects only the haircut `_amount`, and stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (lines 411–421). The user's claim basis is *not* removed from `withdrawsRequests`/`withdrawsRequestsByEpoch` — it is meant to be cleared later by `_claimLossAdjustedWithdrawRequest`.

3. `_claimLossAdjustedWithdrawRequest` resolves the loss epoch solely from `lastWithdrawRequest[_user]` (lines 789–792). If the marker now holds a later epoch `M`, `lossRecoveryPriceByEpoch[M]` is 0 and the function returns without clearing epoch `L`'s basis.

4. `_claimFundedWithdrawRequest` then pays `normalAmount = withdrawsRequests[_user]` — which still includes the epoch-`L` haircut basis — at par via `_transferFundedClaim` (lines 338–349). The guard `epochNumber <= lastWithdrawRequest[_user]` only enforces a one-epoch wait; it does not detect that part of the aggregate was never fully funded.

The broken invariant is "one receipt, one funded payout": the borrower funded `L_amount * lossRecoveryPrice / RECOVERY_FULL`, but the user extracts `L_amount`, i.e., an excess of `L_amount * (1 - lossRecoveryPrice / RECOVERY_FULL)` paid from underlying held by the strategy for other claimants.

### Impact Explanation
Any unprivileged tranche holder (KYC-passing lender) can unilaterally convert a haircut receipt into a par receipt. The excess payout comes directly from strategy-held underlying that is reserved for other users' funded withdraws, instant-withdraw claims, or the default recovery reserve. Either later claimants are left insolvent/unpayable (permanent freezing of their funded claims), or the attacker directly steals funds earmarked for them. Loss magnitude scales with the haircut: with a 50% loss recovery price, the attacker doubles their epoch-L payout, stealing the difference.

### Likelihood Explanation
High. Requires only: (a) a realized `stopEpochWithDuration` loss while the attacker has a pending receipt — an expected protocol event, not attacker-controlled; (b) the attacker submits any second withdraw request in a subsequent epoch and waits one epoch. No privileged cooperation, no timing race, no reentrancy. The write path (`lastWithdrawRequest[_user] = currentEpoch`) and the keyed lookup are unconditional; no skim, flag, or epoch guard detects the stranded epoch-L basis. One caveat: the beginning of `requestWithdraw` (before line 271) was not fully inspected — if an unseen early check reverts when an unclaimed loss-adjusted receipt exists, the attack is blocked, though the visible code comments ("claim both requests") indicate requests are intended to be additive.

### Recommendation
Track pending claim epochs per user rather than relying on the single-slot `lastWithdrawRequest`. Options:
- In `_claimLossAdjustedWithdrawRequest`, iterate or track all epochs with `lossRecoveryPriceByEpoch != 0` for the user (e.g., a per-user list of loss epochs), not just `lastWithdrawRequest`; or
- In `requestWithdraw`, revert if the user has any unclaimed basis in an epoch with `lossRecoveryPriceByEpoch[epoch] != 0`; or
- When a new request is made, first settle/clear any prior loss-adjusted claim (`_clearWithdrawClaimForEpoch` for the old epoch and pay the haircut amount) before overwriting `lastWithdrawRequest`.
Additionally, `_claimFundedWithdrawRequest` could subtract per-epoch loss-adjusted basis from `normalAmount` before paying par.

### Proof of Concept
Foundry fork test sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testLossReceiptRepaperedAtPar() external {
    // Setup: fixed-APR epoch vault, attacker is a KYC'd AA lender
    idleCDO.depositAA(10_000 * ONE_SCALE);
    uint256 tranches = _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);

    // Epoch L: attacker requests withdraw of all tranches
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // or via queue
    uint256 epochL = strategy.epochNumber();

    // Borrower underpays at stopEpoch -> collectWithdrawFunds partial,
    // lossRecoveryPriceByEpoch[epochL] = e.g. 50%
    uint256 owed = strategy.pendingWithdraws();
    deal(defaultUnderlying, borrower, owed / 2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(/* loss params so only ~50% of pendingWithdraws funded */);
    assertGt(strategy.lossRecoveryPriceByEpoch(epochL), 0);

    // Epoch M: attacker requests a second (tiny) withdraw, overwriting
    // lastWithdrawRequest[attacker] to epoch M
    _depositWithUser(attacker, 1 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Let epoch M finish fully funded
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // Claim: _claimLossAdjustedWithdrawRequest looks up epoch M (price 0, returns),
    // _claimFundedWithdrawRequest pays withdrawsRequests[attacker] (incl. epoch-L basis) at par
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    uint256 expectedParPayout = tranches + 1 * ONE_SCALE; // full basis, no haircut
    assertEq(underlying.balanceOf(attacker) - balPre, expectedParPayout,
        'haircut receipt paid at par');
    // Strategy is now short L_amount * (1 - lossRecoveryPrice) for other claimants.
}
```