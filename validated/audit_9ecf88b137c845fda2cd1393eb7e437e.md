### Title
Loss-adjusted epoch haircut only applied to the latest request epoch lets earlier pending receipts be claimed at par, draining funded withdraw liquidity - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` computes a single `lossRecoveryPriceByEpoch[epochNumber]` haircut over the *aggregate* `pendingWithdraws` basis, but `_claimLossAdjustedWithdrawRequest` only applies that haircut to the receipt stored under `lastWithdrawRequest[_user]` — one epoch's receipts. A user with pending receipts spanning multiple request epochs (explicitly supported: "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests") gets the newest-epoch receipt haircut while all earlier-epoch receipts are paid at par via `_claimFundedWithdrawRequest`, even though the borrower only funded `pendingBasis * recoveryPrice`. Like CVE-2016-4079, an unverified identifier (the epoch key chosen for the haircut) is trusted to describe an aggregate object it only partially covers, producing an out-of-bounds-style write: claims exceed funded balance.

### Finding Description
In `requestWithdraw` the vault records `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `lastWithdrawRequest[_user] = currentEpoch`, while `pendingWithdraws` accumulates the aggregate across *all* epochs (`contracts/strategies/idle/IdleCreditVault.sol` [1](#0-0) ).

When `stopEpochWithDuration` passes a nonzero `_lossAmount`, `previewLossAdjustedWithdrawFunds` splits the loss pro rata over the *total* pending basis and `collectWithdrawFunds` stores one price `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis`, zeroing `pendingWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol` [2](#0-1) ). The funding transferred to the strategy is therefore `pendingBasis * price`.

At claim time, `_claimLossAdjustedWithdrawRequest` looks up `lossEpoch = lastWithdrawRequest[_user]` and calls `_clearWithdrawClaimForEpoch(_user, lossEpoch, false)`, which clears only `withdrawsRequestsByEpoch[_user][lossEpoch]` and subtracts only that epoch's `normalAmount` from `withdrawsRequests[_user]` (`contracts/strategies/idle/IdleCreditVault.sol` [3](#0-2) ). Because `lastWithdrawRequest[_user] == lossEpoch`, it is reset to 0, so `_claimFundedWithdrawRequest`'s `epochNumber <= lastWithdrawRequest` guard passes and it pays the *entire remaining* `withdrawsRequests[_user]` — including older-epoch receipts that were haircut in the aggregate funding — at par via `_transferFundedClaim` (`contracts/strategies/idle/IdleTranches--012`, lines 319-349). The guard inside `requestWithdraw` (lines 263-271) only forces a claim before a *new* request when the last epoch already has a stored loss price; it does nothing for a haircut applied retroactively to older pending receipts at stop time.

Broken invariant: one receipt one payout / loss waterfall. Total payouts = `basis_old + basis_new*price` while total funded = `(basis_old + basis_new)*price`. The excess `basis_old * (1 - price)` is paid from underlyings that belong to other pending claimants.

### Impact Explanation
Any user (a KYC-passing lender / tranche-token holder) can make a funded-epoch receipt escape the realized loss and be paid at par. Since the strategy only received `pendingBasis * price` from the borrower, each such claim directly overdraws the pool; other users' funded withdraw claims become unpayable (permanent freezing of their claims / direct insolvency). Loss scales with `basis_old * (1 - recoveryPrice)` per multi-epoch claimer — with a 10% pending-loss haircut and a 100k earlier receipt, ~10k underlying is stolen per occurrence.

### Likelihood Explanation
Requires only: (1) honest manager calls `stopEpochWithDuration` with a nonzero `_lossAmount` while `pendingWithdraws > 0` (a designed loss path, exercised in tests), and (2) an attacker holding pending receipts from ≥2 request epochs, achievable by requesting a withdraw, not claiming, then requesting again — documented normal behavior. No privileged action, oracle manipulation, or default is needed. Attacker is an ordinary KYC'd user; all honest-role assumptions hold.

### Recommendation
Apply the epoch haircut to *all* of a user's receipts that were pending when the loss epoch was funded, not only the `lastWithdrawRequest` epoch. Concretely, either (a) iterate/clear all `withdrawsRequestsByEpoch[_user][*]` ≤ the loss epoch and apply `lossRecoveryPrice` to each before the funded-claim path, or (b) block `requestWithdraw` whenever the user has any unclaimed pending receipt (`_hasWithdrawRequest`-style check for the non-default path), so a single pending receipt is always bound to a single epoch haircut. Also make `_claimFundedWithdrawRequest` subtract any basis already haircut so aggregate payouts can never exceed aggregate funding.

### Proof of Concept
Foundry fork sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol` `_startEpochAndCheckPrices` / `_createLoss` helpers):

```solidity
function testMultiEpochReceiptEscapesHaircut() external {
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // KYC'd attacker + other LPs
    _startEpochAndCheckPrices(0);

    // Epoch N: attacker requests withdraw of 50k, does NOT claim
    idleCDO.requestWithdraw(50_000 * ONE_SCALE, address(AAtranche));

    // Epoch N stops cleanly (or just advances); attacker requests again in epoch N+1
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(apr, 0, duration, 0);
    // attacker does not claim; deposits keep epoch running
    idleCDO.requestWithdraw(30_000 * ONE_SCALE, address(AAtranche)); // allowed: no loss price yet

    // Epoch N+1 stop realizes a loss on pending receipts (honest manager action)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = 80_000 * ONE_SCALE;
    uint256 loss = pendingBasis / 10; // 10% haircut -> recoveryPrice 0.9e18
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(apr, 0, duration, loss);

    // Strategy was funded only pendingBasis * 0.9 = 72k.
    // Attacker claims: epoch N+1 receipt (30k) haircut -> 27k,
    // epoch N receipt (50k) paid AT PAR via _claimFundedWithdrawRequest.
    idleCDO.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker);
    assertEq(got, 77_000 * ONE_SCALE);       // 27k + 50k
    // funded was 72k -> 5k taken from other claimants' funded claims;
    // remaining claimants' _transferFundedClaim reverts on insufficient balance.
}
```

Expected result: `got == 77k` against only `72k` funded, proving the older-epoch receipt bypassed the haircut and permanently froze other users' funded claims.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-801)
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
  }
```
