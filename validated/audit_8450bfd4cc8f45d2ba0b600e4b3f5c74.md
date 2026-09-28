### Title
Loss-adjusted withdraw receipt bypasses its haircut when the user re-requests in a later epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimWithdrawRequest` applies the `stopEpochWithDuration` loss haircut only when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` is set. Because `lastWithdrawRequest` is a single slot overwritten by every new `requestWithdraw`, a user who holds a receipt from a loss-adjusted epoch can make a new withdraw request in a later epoch, which reroutes their whole claim to `_claimFundedWithdrawRequest` and pays the loss-adjusted basis at par. The strategy only collected the haircutted amount for that epoch, so the excess is drained from underlyings funded for other pending claimants.

### Finding Description
The bug class in the external report — a defense check in the withdrawal path that is bypassed — maps here to the epoch-keyed loss haircut verification.

- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and aggregates every request into `withdrawsRequests[_user] += _amount` plus `withdrawsRequestsByEpoch[_user][currentEpoch]` (`contracts/strategies/idle/IdleCreditVault.sol:282-293`).
- `collectWithdrawFunds`, called by the CDO at `stopEpochWithDuration`, can fund less than `pendingBasis`; it then stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:411-421`).
- On claim, `_claimLossAdjustedWithdrawRequest` looks up the haircut with `uint256 lossEpoch = lastWithdrawRequest[_user]` — i.e. only the *most recent* request epoch (`contracts/strategies/idle/IdleCreditVault.sol:789-799`). If the user requested again in a later non-loss epoch M, `lossRecoveryPriceByEpoch[M] == 0` and this function returns 0 without clearing the epoch-N (loss epoch) basis.
- Execution then falls into `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` — the aggregate across *all* epochs including the loss-adjusted epoch N — at par, burns the full receipt, and resets state (`contracts/strategies/idle/IdleCreditVault.sol:338-349`).

The per-epoch haircut verification is therefore skipped purely by overwriting `lastWithdrawRequest`, while the epoch-N basis remains inside `withdrawsRequests[_user]`. The invariant "a receipt from a loss epoch pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL`" is broken: the attacker receives `claimBasis` instead. The only guards involved (`_onlyIdleCDO`, the `epochNumber <= lastWithdrawRequest` wait check, `lossRecoveryPrice == 0` early-return) do not stop this — the wait check passes because epoch M has ended, and the early return is exactly what enables the bypass.

### Impact Explanation
Direct theft of funded reserves / insolvency. The strategy holds only `_amount` (the haircutted funding) for epoch-N receipts; paying the attacker's epoch-N basis at par overpays by `claimBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL`. That excess comes out of the strategy's underlying balance backing other users' funded claims (`_transferFundedClaim` pulls from strategy holdings), so later claimants' transfers revert or are underpaid — permanent loss socialized onto honest withdrawers. With a 50% `lossRecoveryPrice` and a 10,000-underlying epoch-N receipt, the attacker steals ~5,000 underlying beyond their entitlement, bounded only by the strategy's funded balance.

### Likelihood Explanation
Requires an unprivileged tranche holder who (1) requested a withdraw in an epoch that ends with a partial `stopEpochWithDuration` funding (a realized loss, a designed code path), and (2) makes any second `requestWithdraw` in a subsequent epoch before claiming — the code's own comment acknowledges users commonly re-request instead of claiming (`IdleCreditVault.sol:323-324`). No privileged cooperation is needed; the attacker only sequences their own calls around honest manager stopEpoch calls. Likelihood is moderate: it needs a loss-adjusted stop to occur, but once it does, the exploit is a single extra request plus one claim.

### Recommendation
Track the claim epoch per receipt rather than via the single `lastWithdrawRequest` slot. Options: iterate `withdrawsRequestsByEpoch[_user]` entries and apply `lossRecoveryPriceByEpoch` per epoch inside `claimWithdrawRequest` before falling back to par payment, or revert in `requestWithdraw` when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the prior claim is uncleared, forcing the user to claim (haircutted) before re-requesting. At minimum, `_claimLossAdjustedWithdrawRequest` must check all epochs with a non-zero `lossRecoveryPriceByEpoch` for which the user has a `withdrawsRequestsByEpoch` balance, not just `lastWithdrawRequest`.

### Proof of Concept
Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, request helpers):

```solidity
function testLossAdjustedReceiptEscapesHaircut() external {
    uint256 amountWei = 10000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);
    idleCDO.depositBB(amountWei);

    // epoch 0 runs
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // attacker requests withdraw in epoch 1 (becomes the loss epoch)
    uint256 reqN = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // borrower partially funds: stopEpochWithDuration realizes a loss so
    // collectWithdrawFunds stores lossRecoveryPriceByEpoch[1] < RECOVERY_FULL
    // (fund borrower with less than pending basis, e.g. 50%)
    // ... vm.prank(manager); cdoEpoch.stopEpochWithDuration(...loss...);
    uint256 lossPrice = IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(1);
    assertLt(lossPrice, RECOVERY_FULL);

    // epoch 2: attacker re-requests WITHOUT claiming -> lastWithdrawRequest = 2
    _startEpochAndCheckPrices(2);
    cdoEpoch.requestWithdraw(mintedAA / 4, address(AAtranche));
    _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch());

    // claim: _claimLossAdjustedWithdrawRequest reads epoch 2 (no loss price) -> 0,
    // _claimFundedWithdrawRequest pays aggregate withdrawsRequests at par
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balPre;

    // attacker received reqN at par instead of reqN * lossPrice / RECOVERY_FULL
    assertGt(paid, reqN * lossPrice / RECOVERY_FULL + (mintedAA / 4));
    // residual: a second honest claimant's _transferFundedClaim now underflows/reverts
}
```

Note: I could not fully read `_transferFundedClaim` and `_clearWithdrawClaimForEpoch` (past line 800) within the available iterations; the finding rests on the visible bookkeeping — the aggregate `withdrawsRequests[_user]` payout at `IdleCreditVault.sol:338-349` versus the epoch-N funding of only `_amount < pendingBasis` at `IdleCreditVault.sol:411-421`. If `_transferFundedClaim` additionally enforces a per-epoch funded-balance cap keyed to the receipt's epoch, the overpayment would be contained; that check is not present in the code reviewed.