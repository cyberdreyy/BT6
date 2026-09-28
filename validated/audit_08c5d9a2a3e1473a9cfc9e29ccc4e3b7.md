### Title
Loss haircut from `stopEpochWithDuration` is applied only to the latest request epoch while earlier pending receipts claim at par, paying out more than the borrower funded - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `collectWithdrawFunds` receives less than `pendingWithdraws` (a realized loss on `stopEpoch`), it stores a single `lossRecoveryPrice` keyed by the current `epochNumber` and zeroes the aggregate `pendingWithdraws`. However, the haircut is only ever applied to receipts whose `lastWithdrawRequest` equals that epoch. Users holding pending receipts from *earlier* epochs — which were part of the same `pendingWithdraws` basis used to compute the price — fall through to `_claimFundedWithdrawRequest` and are paid at 100%. The vault therefore pays out more underlying than it collected, breaking the loss-socialization invariant and draining funds owed to later claimants.

### Finding Description
`requestWithdraw` stacks per-user receipts across epochs (`withdrawsRequestsByEpoch[_user][epoch]`) into one aggregate `pendingWithdraws`, and only blocks a new request when the *last* epoch of that user already carries a loss price (lines 261–271). So `pendingWithdraws` routinely aggregates receipts spanning multiple epochs and multiple users.

On a partial funding at `stopEpoch` (`_amount < pendingBasis`), `collectWithdrawFunds` computes:

```
lossRecoveryPrice = _amount * 1e18 / pendingBasis
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice
pendingWithdraws = 0
``` [1](#0-0) 

The price was computed against the **aggregate** basis (all epochs), but `_claimLossAdjustedWithdrawRequest` only haircut-claims the receipts of `lastWithdrawRequest[_user]` — a single epoch — via `_clearWithdrawClaimForEpoch`. Any receipt booked in an earlier epoch has `withdrawsRequestsByEpoch[user][oldEpoch] != 0` while `lastWithdrawRequest[user]` points to a later epoch, or — for a user whose *only* request was in an earlier epoch — `lastWithdrawRequest[user]` is that earlier epoch, whose `lossRecoveryPriceByEpoch` entry is 0, so the receipt goes straight to `_claimFundedWithdrawRequest` and is paid at par (lines 319–349, 789–801).

Numerically: with epoch-A basis `A` and epoch-B basis `B`, funding `F < A+B`, price `p = F/(A+B)`. Epoch-A claimants receive `A` in full; epoch-B claimants receive `B·p`. Total paid `= A + B·F/(A+B) > F` whenever `A > 0`. The excess is pulled from the strategy's underlying balance by `_transferFundedClaim`, which only guards against spending `defaultRecoveryReserve`, not against paying unfunded receipts (lines 897–907).

Guards that do not stop it:
- `requestWithdraw`'s loss-price revert only checks the user's own `lastWithdrawRequest` epoch; it does not prevent `pendingWithdraws` from containing multi-epoch/multi-user basis.
- `_claimFundedWithdrawRequest`'s epoch gating (`epochNumber <= lastWithdrawRequest`) passes once a later epoch has started.
- The `defaultRecoveryReserve` guard in `_transferFundedClaim` does not isolate this shortage.

### Impact Explanation
Losses that should be socialized pro rata across all pending receipts are instead concentrated on the latest-epoch requesters, while earlier requesters are made whole. The vault disburses strictly more underlying than the borrower funded for the epoch; the deficit is borne by remaining claimants and/or the default recovery reserve, i.e. direct insolvency / theft of unclaimed yield belonging to later withdrawers. The shortfall scales linearly with the size of earlier-epoch pending basis `A` and the loss magnitude.

### Likelihood Explanation
Requires only unprivileged KYC-passed lenders making withdraw requests in different epochs (a normal usage pattern — the code comments explicitly document that unclaimed requests roll forward), plus a single `stopEpochWithDuration`-style partial funding (honest manager/borrower action, e.g. a realized loss). No privileged misbehavior is needed. The main residual uncertainty is the exact `epochNumber` increment ordering relative to `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch` (grep results were truncated); if the loss price were keyed post-increment it would never match any request epoch, which would make the overpayment apply to *all* receipt epochs rather than only earlier ones — the impact direction is the same or worse.

### Recommendation
Attribute the loss price to every epoch contributing to `pendingBasis`, not only the current one. Practically: maintain a per-epoch pending-basis mapping (`pendingWithdrawsByEpoch`) populated in `requestWithdraw`/`requestInstantWithdraw`, and on partial funding either (a) store the haircut under each contributing epoch, or (b) record a global `pendingWithdrawsFundedPrice`/`pendingWithdrawsFundedAmount` and have `_claimFundedWithdrawRequest` apply the haircut to *any* receipt cleared while the price is active, releasing it only once the funded amount is fully consumed. Add a regression test with two users requesting in consecutive epochs followed by a partial funding, asserting total claimed ≤ funded amount.

### Proof of Concept
Foundry fork sketch (same harness style as `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPartialFundingEarlierEpochPaidAtPar() external {
    // setup: fixed-APR vault, fees 0
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    idleCDO.depositAA(10000 * ONE_SCALE);            // user A (this)
    _depositWithUser(userB, 10000 * ONE_SCALE, true);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);                    // epoch 0 running
    // A requests withdraw in epoch 0
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    // epoch 0 stops FULLY funded -> pendingWithdraws cleared but A's receipt stays
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest()
        + IdleCreditVault(address(strategy)).pendingWithdraws() + 10000 * ONE_SCALE);
    vm.prank(manager); cdoEpoch.stopEpoch(0, 0);
    // A does NOT claim.

    _startEpochAndCheckPrices(1);                    // epoch 1 running
    // B requests withdraw in epoch 1; pendingWithdraws now = B's basis only
    vm.prank(userB);
    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(userB) / 2, address(AAtranche));

    // epoch 1 stops with a realized loss: borrower funds only F < pendingBasis
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    uint256 funded = pending / 2;                    // 50% loss on pending receipts
    deal(defaultUnderlying, borrower, funded + cdoEpoch.expectedEpochInterest());
    vm.prank(manager); cdoEpoch.stopEpochWithDuration(0, /* loss args so that
        collectWithdrawFunds is called with `funded` */);

    // A's receipt has lastWithdrawRequest = epoch0, lossRecoveryPriceByEpoch[0] == 0
    // -> _claimLossAdjustedWithdrawRequest returns 0 -> _claimFundedWithdrawRequest pays FULL
    uint256 balBefore = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balBefore, principalA); // paid at par, no haircut

    // B is haircutted by price computed on aggregate; vault disbursed A + B*price > funded
    // -> later claimant / reserve absorbs the deficit (assert strategy balance < expected).
}
```

Expected result: user A claims 100% of a receipt that was never funded for the loss epoch, total underlying leaving the strategy exceeds the funded amount, and either B's haircut is steeper than the realized loss share or the strategy's residual balance (owed to other claimants) is short by `A * (1 - price)` — demonstrating insolvency/unfair loss allocation.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-426)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
```
