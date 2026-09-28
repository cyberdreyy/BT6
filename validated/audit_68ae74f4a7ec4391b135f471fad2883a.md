### Title
Post-loss withdraw requests are keyed to the stale loss epoch and paid out at the haircut price — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` records a realized stop-epoch loss in `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is only incremented later (on the next `deposit` while `isEpochRunning`). A withdraw request opened during the buffer after that lossy stop is recorded under the same epoch index. On claim, `_claimLossAdjustedWithdrawRequest` matches `lastWithdrawRequest[user] == lossEpoch` and pays the new, fully-funded receipt at the stale haircut price, permanently confiscating `(1 - lossRecoveryPrice)` of the user's funds.

### Finding Description
The analog of "reading uninitialized memory" is reading **stale/uninitialized epoch-keyed state**: `lossRecoveryPriceByEpoch` is indexed only by `epochNumber`, with no flag distinguishing receipts created *before* the loss from receipts created *after* it.

Sequence:
1. Epoch N ends via `stopEpochWithDuration(_lossAmount)` (or any partial-funding stop). `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws` [1](#0-0) . `epochNumber` is still N.
2. During the buffer, a KYC'd lender calls `cdoEpoch.requestWithdraw` (allowed when `!isEpochRunning` and `epochEndDate() != 0`). `requestWithdraw` stores the receipt at `withdrawsRequestsByEpoch[user][N]` and `lastWithdrawRequest[user] = N` [2](#0-1) . The guard that forces claiming a loss-adjusted receipt first passes because `withdrawsRequestsByEpoch[user][lossEpoch]` was still 0 at request time [3](#0-2) .
3. `pendingWithdraws += _amount` again, so the *next* stop funds this receipt at par via the `else` branch of `collectWithdrawFunds` [4](#0-3) .
4. When the user calls `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` fires first: it reads `lossEpoch = lastWithdrawRequest[user] = N`, sees `lossRecoveryPriceByEpoch[N] != 0`, clears the whole epoch-N basis and pays only `claimBasis * lossRecoveryPrice / 1e18` [5](#0-4) . `_claimFundedWithdrawRequest` then finds nothing left, so the missing `(1 - lossRecoveryPrice)` fraction is never paid.

### Impact Explanation
A user whose request postdates the loss is nonetheless haircut by it. With a 30% realized loss (`lossRecoveryPrice = 0.7e18`), a 100k USDC request funded at par pays out only 70k; the 30k remainder stays locked in the strategy (the borrower's funding was already collected), permanently freezing the difference and inflating other claimants' backing. Broken invariant: one receipt one payout / fair mint-burn — a fully funded receipt must pay at par.

### Likelihood Explanation
Requires a partial-loss `stopEpochWithDuration` (or underfunded `collectWithdrawFunds`) followed by a buffer-period withdraw request before `epochNumber` increments — a routine sequence, not dependent on privileged misbehavior. Any lender in a vault that has ever taken a stop-loss is exposed.

### Recommendation
Key loss-recovery pricing by the request's funding epoch, not the request epoch: e.g., store a per-epoch `lossApplied` flag that excludes receipts recorded after the loss was written (compare `lastWithdrawRequest` to the epoch in which `collectWithdrawFunds` ran and require the receipt to have contributed to that loss's `pendingBasis`), or increment `epochNumber`/record a `lossProcessedEpoch` marker in `collectWithdrawFunds` so post-loss requests cannot collide with the haircut epoch.

### Proof of Concept
Foundry fork sketch (extends `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPostLossRequestHaircutted() external {
    // epoch N: user1 deposits, requests withdraw -> pendingWithdraws = P
    _depositWithUser(user1, 100_000e6, true);
    _requestWithdrawWithUser(user1, trancheAmount1);
    // borrower returns funds minus 30% loss; manager calls stopEpochWithDuration
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(lossAmount, duration); // triggers collectWithdrawFunds partial
    // lossRecoveryPriceByEpoch[N] = 0.7e18; epochNumber still N during buffer

    // user2 (fresh, no prior receipt) requests withdraw in the buffer of epoch N
    _depositWithUser(user2, 100_000e6, true);
    _requestWithdrawWithUser(user2, trancheAmount2);
    assertEq(strategy.lastWithdrawRequest(user2), strategy.epochNumber()); // == N, the loss epoch

    // next epoch fully funds user2's request at par via collectWithdrawFunds
    _startEpochAndCheckPrices(0);
    _stopCurrentEpochFullyFunded();

    // user2 claim is haircut by the stale epoch-N loss price
    uint256 pre = underlying.balanceOf(user2);
    vm.prank(user2);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(user2) - pre;
    uint256 expected = trancheAmount2; // funded at par
    assertLt(paid, expected); // ~30% permanently withheld
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L277-294)
```text
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L422-426)
```text
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
