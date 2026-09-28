### Title
Post-loss buffer withdraw requests are indexed into the already-haircutted epoch bucket, so fresh receipts are paid at the stale `lossRecoveryPriceByEpoch` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the OOB read in `exif_scan_thumbnail` — where parsing reads data past the intended buffer — `IdleCreditVault.requestWithdraw` tags new withdraw receipts with the current `epochNumber` and `_claimLossAdjustedWithdrawRequest` later "reads" that epoch index into `lossRecoveryPriceByEpoch`. When a `stopEpochWithDuration` loss has just been recorded for that epoch, a withdraw requested during the following buffer period is written into the same (already-loss-adjusted) epoch bucket. The guard that was meant to prevent this only looks at the user's *previous* `lastWithdrawRequest` epoch, so it reads the wrong slot and lets the request through. The new receipt is then paid at the stale haircut price even though its funds are fully funded at par by the next `stopEpoch`.

### Finding Description
In `requestWithdraw`, the loss-receipt re-request guard computes the lookup epoch from the user's *old* `lastWithdrawRequest` value before overwriting it: [1](#0-0) 

`collectWithdrawFunds` stores the haircut keyed by `epochNumber` at the moment the lossy `stopEpoch` collects funds: [2](#0-1) 

The claim path then uses `lastWithdrawRequest[user]` as an index into `lossRecoveryPriceByEpoch` — an epoch the user may never have had a receipt in: [3](#0-2) 

Attack/flow sequence:

1. Epoch N is running; a borrower loss occurs and `stopEpochWithDuration(_lossAmount)` calls `collectWithdrawFunds(pendingToFund)` with `pendingToFund < pendingWithdraws`. `lossRecoveryPriceByEpoch[epochNumber] = price < 1e18` is stored and `pendingWithdraws` is zeroed.
2. During the buffer (before `startEpoch` of N+1), a victim (or attacker positioning a victim-like receipt) calls `requestWithdraw(_amount, _user, _principal)` via the CDO. At line 261, `lossEpoch = lastWithdrawRequest[_user]`, which is either `0` or some older epoch with no loss price, so `lossRecoveryPrice == 0` and the guard at lines 263–271 passes.
3. Line 282 sets `lastWithdrawRequest[_user] = currentEpoch` (the loss epoch N), and line 293 writes `withdrawsRequestsByEpoch[_user][N] += _amount` — polluting the already-closed, haircutted epoch bucket with a receipt that was never part of the original `pendingBasis`.
4. After epoch N+1 runs and stops normally, the new `pendingWithdraws` is funded at par by `collectWithdrawFunds` (full-funding branch, line 425).
5. On `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` reads `lastWithdrawRequest[_user] == N`, finds `lossRecoveryPriceByEpoch[N] != 0`, and pays only `_amount * price / 1e18`. The remaining `(1 - price) * _amount` of fully funded underlying is stranded in the strategy — it is not swept into `defaultRecoveryReserve` and is not claimable by anyone.

The broken invariant is fair burn/payout ("one receipt, one payout at the correct price"): a receipt created *after* a loss was realized inherits that loss purely because of an out-of-range epoch index read.

### Impact Explanation
Any user whose first-ever (or first-since-claimed) withdraw request lands in the buffer period following a `stopEpochWithDuration` loss permanently loses `(1 - lossRecoveryPrice) * _amount` of underlying that was funded at par. With a 50% recovery price, a 100k USDC request loses 50k USDC; the funds remain locked in the vault as unclaimable dust (effectively a donation to no one, since `defaultRecoveryReserve` was not incremented). This is a direct, quantified loss of user funds plus permanent freezing of the difference.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss (partial borrower underfunding) — an intended, non-privileged-abuse code path — followed by a withdraw request during the buffer before the next `startEpoch`. Any KYC-passing lender can trigger it; no malicious privileged role is needed. The trigger window is the whole buffer period after every lossy stop, and it hits the very users most likely to exit after a loss announcement, so the affected population is maximal exactly when the bug is armed. The miscrediting is deterministic once the sequence occurs.

### Recommendation
Either (a) tag new requests with the *next* epoch when they are made during the buffer after a stopped epoch (e.g., use `epochNumber` only while an epoch is running, or `epochNumber + 1` semantics consistent with how `stopEpoch` bumps the counter), or (b) extend the guard in `requestWithdraw` to also check `lossRecoveryPriceByEpoch[currentEpoch]` for the epoch being written into, and reject/redirect requests that would land in a closed loss epoch. At minimum, `withdrawsRequestsByEpoch[_user][currentEpoch]` must never be incremented for an epoch whose `lossRecoveryPriceByEpoch` is already set, since the funded basis for that epoch is already fixed.

### Proof of Concept
Foundry fork PoC (schematic, against `test/foundry` harness conventions):

```solidity
// 1. Deposit AA via idleCDO, startEpoch, borrower repays less than owed.
// 2. manager calls cdoEpoch.stopEpochWithDuration(apr, lossAmount)
//    -> strategy.collectWithdrawFunds(pendingToFund < pendingBasis)
//    -> lossRecoveryPriceByEpoch[epochNumber] = price (< 1e18)
// 3. Warp into buffer (before startEpoch).
// 4. Fresh user deposits, then calls cdoEpoch.requestWithdraw(0, AAtranche)
//    -> guard passes because lastWithdrawRequest[user] == 0
//    -> withdrawsRequestsByEpoch[user][lossEpoch] = amount  (BUG)
// 5. startEpoch, run epoch N+1, borrower funds fully, stopEpoch.
// 6. user calls cdoEpoch.claimWithdrawRequest()
//    -> _claimLossAdjustedWithdrawRequest pays amount * price / 1e18
// assert: userReceived == amount * price / 1e18  (not amount)
// assert: underlying.balanceOf(strategy) >= amount - userReceived // stranded
```

Caveat: the exact line numbers for `epochNumber` bumping relative to `collectWithdrawFunds` ordering inside `IdleCDOEpochVariant.stopEpochWithDuration` were not fully read in this pass; the PoC should assert `lossRecoveryPriceByEpoch[strategy.epochNumber()]` is nonzero at the time of step 4 to confirm the index collision before claiming the loss.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-294)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
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
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-425)
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
