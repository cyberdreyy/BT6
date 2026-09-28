### Title
Earlier-epoch pending withdraw receipts escape the `stopEpochWithDuration` loss haircut because `_claimLossAdjustedWithdrawRequest` only applies `lossRecoveryPriceByEpoch` to the `lastWithdrawRequest` epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The rsync bug (a stale/negative index dereferencing the wrong buffer entry) maps to `IdleCreditVault.requestWithdraw`/`claimWithdrawRequest`: a user's pending withdraw receipts are tracked per-epoch in `withdrawsRequestsByEpoch[user][epoch]`, but loss attribution uses a single pointer, `lastWithdrawRequest[user]`. When a user holds unfunded receipts in two different epochs, `collectWithdrawFunds` computes one haircut over the aggregate `pendingWithdraws` (which includes both epochs), yet `_claimLossAdjustedWithdrawRequest` only clears and haircuts the epoch stored in `lastWithdrawRequest`. The older receipt is then paid at par through `_claimFundedWithdrawRequest`, even though the borrower only funded the haircut amount. The deficit is socialized onto the strategy's funded balance / other claimants.

### Finding Description
- `requestWithdraw` records `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and sets `lastWithdrawRequest[_user] = currentEpoch` for every request [1](#0-0) .
- The guard at lines 263-270 only blocks a *new* request when the epoch pointed to by `lastWithdrawRequest` already has a non-zero `lossRecoveryPrice`. It does not block a second pending receipt while the previous one is still unfunded and loss-free [2](#0-1) .
- `collectWithdrawFunds` treats `pendingWithdraws` as one bucket: on `_amount < pendingBasis` it stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws`, i.e. the haircut is implicitly applied to *all* outstanding receipts, across all request epochs [3](#0-2) .
- On claim, `_claimLossAdjustedWithdrawRequest` reads `lossEpoch = lastWithdrawRequest[_user]` and `_clearWithdrawClaimForEpoch` only zeroes `withdrawsRequestsByEpoch[_user][lossEpoch]` and decrements the aggregate by that epoch's amount; receipts from earlier epochs stay in `withdrawsRequests[_user]` [4](#0-3) [5](#0-4) .
- `_clearWithdrawClaimForEpoch` then resets `lastWithdrawRequest[_user] = 0`, so the funded-claim epoch gate `epochNumber <= lastWithdrawRequest[_user]` passes, and `_claimFundedWithdrawRequest` pays the remaining older-epoch basis at 100% [6](#0-5) [7](#0-6) .

Net effect: `lossRecoveryPrice` was computed as `funded / totalPendingBasis`, but only the latest-epoch slice is paid at that price; the earlier slice is paid at `RECOVERY_FULL` from the same strategy balance. If per-epoch receipts were intended to exist, `collectWithdrawFunds` should instead attribute loss only to the current epoch's receipts, or the claim must iterate all epochs bearing `lossRecoveryPriceByEpoch`.

### Impact Explanation
Direct theft / insolvency: the attacker receives `receipt_A * (1 - lossRecoveryPrice)` more underlying than the loss waterfall funded. Since `pendingWithdraws` was cleared assuming every receipt takes the haircut, the excess comes out of the strategy's underlying balance, diluting other withdraw claimants, APR0 settled claims, or active LPs' redemption value. Quantified example: pending receipts of 100 (epoch A) + 100 (epoch B), borrower funds 150 → `lossRecoveryPrice = 0.75e18`. Attacker claims 75 for epoch B (correct) plus 100 for epoch A at par = 175 total against a strategy that received only 150 for the pending bucket; the extra 25 is taken from other users' funded claims.

### Likelihood Explanation
Requires a KYC-allowed user (in-scope attacker class) to place withdraw requests in two consecutive epochs and a `stopEpochWithDuration`-style partial loss on the second. The two-request precondition is easy: `requestWithdraw` is permissionless per-epoch via the CDO, and the existing guard explicitly permits a second request while the first is merely unfunded. Partial-loss stopEpoch is a designed code path (`previewLossAdjustedWithdrawFunds` / `collectWithdrawFunds`), not an edge case. The APR0 flow has the same shape via `apr0Users[_user].principalEpoch`.

### Recommendation
In `requestWithdraw`, revert when the user has *any* unclaimed unfunded receipt (`withdrawsRequests[_user] != 0` or open `apr0Users` principal), not only a loss-adjusted one; or change `collectWithdrawFunds`/`_claimLossAdjustedWithdrawRequest` to iterate all epochs covered by the aggregate `pendingWithdraws` haircut so every pending receipt is paid at `lossRecoveryPrice`.

### Proof of Concept
Foundry fork sketch (concrete wiring mirrors `test/foundry/IdleCDOEpochQueue.t.sol` loss test at lines 811-858):

```solidity
// setup: standard epoch variant, attacker = KYC'd AA holder
// epoch 1 running: attacker requests withdraw of 100 (receipt epoch A = epochNumber)
cdoEpoch.requestWithdraw(100e18, AA);
// stopEpoch -> epoch 2 buffer (receipt still unfunded, lastWithdrawRequest = A)
_stopCurrentEpoch();
// epoch 2 running: attacker requests another 100 (guard passes: lossRecoveryPriceByEpoch[A] == 0)
cdoEpoch.requestWithdraw(100e18, AA);
// borrower funds only 150 of pendingWithdraws = 200 via stopEpochWithDuration loss
uint256 loss = pendingBasis * 25 / 100;
cdoEpoch.stopEpochWithDuration(interest, 0, duration, loss); // collectWithdrawFunds(150)
// lossRecoveryPriceByEpoch[B] = 150*1e18/200 = 0.75e18
// attacker claims: epoch B slice pays 75, epoch A slice pays 100 at par
uint256 pre = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest(); // -> strategy.claimWithdrawRequest(attacker)
assertEq(underlying.balanceOf(attacker) - pre, 175e18); // funded was only 150 for the bucket
```

Key assertions: `strategy.lossRecoveryPriceByEpoch(epochB) == 0.75e18`, payout for the epoch-A receipt is at par (`withdrawsRequestsByEpoch[A]` cleared only through the funded path), and the strategy's underlying balance is drained below the aggregate funded claims.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-421)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-820)
```text
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L832-836)
```text
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
```
