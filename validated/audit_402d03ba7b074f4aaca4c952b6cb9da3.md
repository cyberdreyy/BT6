### Title
Loss-adjusted withdrawal receipts can escape the haircut by re-anchoring `lastWithdrawRequest` to a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest()` derives the epoch whose haircut should be applied from `lastWithdrawRequest[_user]` — a mutable, user-controlled marker — instead of checking every epoch in which the user actually holds a pending receipt. A lender whose withdrawal request was haircut by `stopEpochWithDuration` can submit a new withdraw request in the following epoch, overwriting `lastWithdrawRequest`. The loss-keyed lookup then misses (`lossRecoveryPriceByEpoch[L+1] == 0`), the haircutted receipt survives in `withdrawsRequests[_user]`, and it is later paid **at par** through `_claimFundedWithdrawRequest()`. This is the same bug class as the reported `withdrawBySnapshot()` issue: a user-supplied/derived anchor is trusted without being validated against the authoritative state (the loss epoch), so a receipt that should be settled under one regime is settled under another.

### Finding Description
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds()` funds less than `pendingWithdraws` and records `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (IdleCreditVault.sol:411-430). Claimants are supposed to be paid `claimBasis * lossRecoveryPrice / RECOVERY_FULL`.

The claim path is:

```
claimWithdrawRequest -> _claimLossAdjustedWithdrawRequest -> _claimFundedWithdrawRequest
```

`_claimLossAdjustedWithdrawRequest()` (IdleCreditVault.sol:789-801) computes `lossEpoch = lastWithdrawRequest[_user]` and only clears receipts for that single epoch via `_clearWithdrawClaimForEpoch`. `requestWithdraw()` (lines ~281-294) unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and adds to `withdrawsRequestsByEpoch[_user][currentEpoch]` — there is no check preventing a user with an unclaimed haircutted receipt from requesting again.

Sequence:
1. Epoch `L` running: attacker requests withdraw; receipt recorded in `withdrawsRequestsByEpoch[attacker][L]`, `lastWithdrawRequest = L`.
2. `stopEpochWithDuration` realizes a loss: `lossRecoveryPriceByEpoch[L] = p < RECOVERY_FULL`, `pendingWithdraws = 0`, `epochNumber = L+1`.
3. Buffer/epoch `L+1`: attacker calls `requestWithdraw` again → `lastWithdrawRequest = L+1`, new receipt added.
4. Epoch `L+2` (after the next `stopEpoch` funds the `L+1` request): attacker calls `claimWithdrawRequest`.
   - `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[L+1] == 0` → returns 0. The haircutted `L` receipt is never cleared.
   - `_claimFundedWithdrawRequest`: `epochNumber (L+2) > lastWithdrawRequest (L+1)` → passes. `normalAmount = withdrawsRequests[attacker]` includes both the haircutted `L` basis and the `L+1` basis → `_transferFundedClaim` pays it all at par.

The `L`-epoch receipt therefore escapes `lossRecoveryPriceByEpoch[L]` entirely.

### Impact Explanation
The vault only received `pendingBasis * p` underlying for epoch `L` receipts. Paying the attacker's `L` receipt at par over-draws the funded pool: the excess is taken from underlyings reserved for other `L`-epoch claimants and honest funded receipts, so later legitimate claims revert on insufficient balance — direct theft from other lenders plus insolvency of the claim pool. Loss is `claimBasis_L * (1 - p/RECOVERY_FULL)` per attacker, bounded only by how much they can stake into the pending bucket; repeatable.

### Likelihood Explanation
Requires only a normal lender position (KYC'd tranche holder): request a withdraw, wait for any realized-loss `stopEpoch` (a normal, honest manager action, e.g. borrower partial repayment), then request again and wait one epoch. No privileged role, no timing race — the attacker unilaterally re-anchors the epoch key. The design even acknowledges re-requesting overwrites the wait marker ("NOTE: if a user does not claim... he will have to wait for another epoch"), but the haircut bookkeeping was not made robust to it.

### Recommendation
In `_claimLossAdjustedWithdrawRequest`, don't trust `lastWithdrawRequest` as the sole loss-epoch key. Either iterate `lossRecoveryPriceByEpoch` over all epochs present in `withdrawsRequestsByEpoch[_user]` (or track a per-user list of unfunded receipt epochs), or revert/`NotAllowed` in `requestWithdraw` while the user's current `lastWithdrawRequest` epoch has a nonzero `lossRecoveryPriceByEpoch` that hasn't been claimed. Equivalently, force settlement of any existing haircutted receipt before accepting a new request from the same user.

### Proof of Concept
Foundry fork PoC (sketch, based on `test/foundry/IdleCreditVault*.t.sol` harness):

```solidity
function testLossReceiptEscapesHaircut() external {
    // --- epoch L running ---
    uint256 dep = 1000e6;
    uint256 tr = _depositWithUser(attacker, dep);   // attacker = KYC'd lender
    _depositWithUser(victim, dep);                  // honest lender, same epoch
    vm.prank(manager); cdoEpoch.startEpoch();

    // both request withdraw in epoch L
    _requestWithdrawWithUser(attacker, tr);
    _requestWithdrawWithUser(victim, tr);

    // --- stopEpochWithDuration with a loss: borrower funds 50% of pending ---
    uint256 pending = strategy.pendingWithdraws();
    deal(address(underlying), borrower, pending / 2, true);
    vm.prank(borrower); underlying.approve(address(cdoEpoch), pending / 2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, /*loss such that price = 0.5*/);
    assertLt(strategy.lossRecoveryPriceByEpoch(strategy.epochNumber() - 1), RECOVERY_FULL);

    // --- epoch L+1: attacker re-requests, overwriting lastWithdrawRequest ---
    vm.prank(manager); cdoEpoch.startEpoch();
    uint256 tr2 = _depositWithUser(attacker, 1e6);
    _requestWithdrawWithUser(attacker, tr2);
    assertEq(strategy.lastWithdrawRequest(attacker), strategy.epochNumber());

    // --- epoch L+2: fund new requests, then attacker claims at par ---
    _stopCurrentEpochFullyFunded();
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimWithdrawRequest();
    // attacker received full basis for the epoch-L receipt, not basis * 0.5
    assertGt(underlying.balanceOf(attacker) - balPre, tr * 5e17 / 1e18);

    // victim's loss-adjusted claim now underflows/reverts -> insolvency
    vm.expectRevert();
    vm.prank(victim); cdoEpoch.claimWithdrawRequest();
}
```

The broken invariant is the loss waterfall / "one receipt one payout": a haircutted pending receipt must always be settled at `lossRecoveryPriceByEpoch` of the epoch it was created in, and never at par through the funded-claim path.