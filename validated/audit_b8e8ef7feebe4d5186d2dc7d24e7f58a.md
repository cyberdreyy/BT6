### Title
Loss-adjusted withdraw receipts escape their haircut and are paid at par when the user files a later withdraw request - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The LibRaw CVE is a boundary error: repeated input sequences write past a fixed-size slot, corrupting adjacent state. The analog in `IdleCreditVault` is the single-slot `lastWithdrawRequest[_user]` marker combined with per-epoch loss pricing. `_claimLossAdjustedWithdrawRequest` looks up the loss price only for `lastWithdrawRequest[_user]`, so any older loss-adjusted receipt that is not in the user's *latest* request epoch falls through to `_claimFundedWithdrawRequest`, which pays the full `withdrawsRequests[_user]` aggregate at par. A user who suffered a `stopEpochWithDuration` loss can therefore escape the haircut entirely — and be paid more than was ever funded — simply by filing one more withdraw request in a later, fully-funded epoch.

### Finding Description
In `requestWithdraw`, every new request overwrites `lastWithdrawRequest[_user] = currentEpoch` and adds to both the aggregate `withdrawsRequests[_user]` and the per-epoch `withdrawsRequestsByEpoch[_user][currentEpoch]` [1](#0-0) . There is no guard preventing a user with an unclaimed receipt from requesting again; the code comments even acknowledge this as supported behavior ("if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests").

When `stopEpochWithDuration` results in partial funding, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` [2](#0-1) . The strategy is only transferred the *discounted* amount.

On claim, `_claimLossAdjustedWithdrawRequest` only considers `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — a single epoch slot [3](#0-2) . If the user's *latest* request epoch has no loss price (price 0 → early return, nothing cleared), execution proceeds to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` — the aggregate including the older loss-epoch receipt — at 100% [4](#0-3) . The gate `epochNumber <= lastWithdrawRequest[_user]` passes once the later epoch has ended.

Broken invariant: one-receipt-one-payout and the loss waterfall. The strategy holds `claimBasis_N * p + claimBasis_M` for the user but pays `claimBasis_N + claimBasis_M`; the difference `claimBasis_N * (1 - p)` is drained from underlying that backs other claimants or active LPs — i.e., haircut escape plus theft of the unfunded remainder.

### Impact Explanation
Direct theft / insolvency. Any user whose withdraw receipt was loss-adjusted in epoch N can recover it at par by requesting another withdraw in a later non-loss epoch and claiming. The over-payment `claimBasis * (1 - lossRecoveryPrice)` is taken from strategy-held underlying reserved for other funded receipts, so later claimants' transfers revert or are short-paid — the haircut that `stopEpochWithDuration` was designed to socialize is instead concentrated on whoever claims last.

### Likelihood Explanation
Requires only unprivileged actions: deposit tranche tokens (KYC'd lender), `requestWithdraw` in a loss epoch, `requestWithdraw` again in the next buffer, wait one epoch, `claimWithdrawRequest`. The privileged calls in between (stop/start epoch) are honest-manager operations in the normal flow. The trigger condition — a `stopEpochWithDuration` partial funding followed by any later epoch — is an ordinary operating mode, not an edge configuration. No default, no APR0, no queue needed.

### Recommendation
Do not let loss-adjusted receipts fall through to the par claim path. Options:
- Track per-user loss epochs: iterate or record all epochs in which the user has a claim cleared at a `lossRecoveryPriceByEpoch` price, and subtract those pieces from `withdrawsRequests[_user]` before the funded claim (as `_clearWithdrawClaimForEpoch` already does for the matched epoch).
- In `requestWithdraw`, either revert/settle when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` (force the user to claim the discounted receipt first), or eagerly apply the haircut to the stored basis at funding time so `withdrawsRequestsByEpoch` always reflects funded value.
- At minimum, in `_claimFundedWithdrawRequest`, exclude any `withdrawsRequestsByEpoch[_user][e]` for epochs `e` with a nonzero `lossRecoveryPriceByEpoch` from the par payout.

### Proof of Concept
Foundry fork PoC outline (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testLossAdjustedReceiptEscapesHaircut() external {
    // 1. User deposits AA, epoch 0 runs and stops normally.
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);

    // 2. User requests withdraw of trancheAmount in epoch 1.
    uint256 req = cdoEpoch.requestWithdraw(userTranches, address(AAtranche)); // lastWithdrawRequest[user] = 1

    // 3. Epoch 1 ends via stopEpochWithDuration with a loss; borrower funds
    //    only p = 50% of pendingWithdraws.
    //    collectWithdrawFunds sets lossRecoveryPriceByEpoch[1] = RECOVERY_FULL/2,
    //    strategy receives only req/2 underlying.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(lossParams...); // partial funding

    // 4. In epoch 2 buffer, user deposits again and files a second requestWithdraw.
    //    lastWithdrawRequest[user] = 2; withdrawsRequests[user] = req + req2.
    idleCDO.depositAA(amount2);
    cdoEpoch.requestWithdraw(userTranches2, address(AAtranche));

    // 5. Epoch 2 stops fully funded (no loss) -> lossRecoveryPriceByEpoch[2] == 0.
    _startEpochAndCheckPrices(2);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, interest); // borrower funds pendingWithdraws in full

    // 6. Claim: _claimLossAdjustedWithdrawRequest uses lossEpoch = 2, price 0 -> skips.
    //    _claimFundedWithdrawRequest then pays req + req2 at PAR,
    //    although only req*1/2 + req2 was ever funded.
    uint256 balBefore = underlying.balanceOf(user);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(user) - balBefore;

    // Attacker receives req + req2 instead of req/2 + req2.
    assertEq(paid, req + req2);                    // escaped haircut
    assertGt(paid, req / 2 + req2);                // over-paid vs funded amount
    // The extra req/2 is drained from other claimants' funded reserve.
}
```

Key assertion: `paid == withdrawsRequests` aggregate at par while the strategy was only credited `claimBasis * lossRecoveryPrice` for epoch 1 — demonstrating theft of `req * (1 - p)` from other receipt holders.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
```text
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
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
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```
