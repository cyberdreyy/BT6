### Title
Loss-adjusted withdrawal receipts are recorded under the post-increment epoch, so `_claimLossAdjustedWithdrawRequest` never matches and haircut receipts are paid at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the JWT `none`-algorithm forgery (a token presented without a valid signature is accepted), a pending withdraw receipt whose funding was haircut by `collectWithdrawFunds` can be claimed without the loss ever being applied: the recovery price is stored under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is incremented inside `deposit()` during the same `stopEpoch` before `collectWithdrawFunds` runs, so the haircut is keyed one epoch ahead of where `_claimLossAdjustedWithdrawRequest` looks it up via `lastWithdrawRequest[_user]`. The "signature" (loss haircut) is never checked, and the claim falls through to `_claimFundedWithdrawRequest`, which pays at par.

### Finding Description
- `requestWithdraw` records the request under `lastWithdrawRequest[_user] = epochNumber` and `withdrawsRequestsByEpoch[_user][currentEpoch]` [1](#0-0) .
- On a lossy `stopEpochWithDuration`, the CDO calls `collectWithdrawFunds(_amount)` with `_amount < pendingWithdraws`; the strategy stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws`, meaning every pending receipt for that epoch should later be paid `claimBasis * lossRecoveryPrice / RECOVERY_FULL` [2](#0-1) .
- However, `deposit()` — invoked by the CDO during the same `stopEpoch` while `isEpochRunning()` is still true — executes `epochNumber += 1` [3](#0-2) . The haircut is therefore written under the *next* epoch, not the epoch in which the receipts were recorded.
- On claim, `_claimLossAdjustedWithdrawRequest` computes `lossEpoch = lastWithdrawRequest[_user]` (the pre-increment epoch) and reads `lossRecoveryPriceByEpoch[lossEpoch]`, which is `0`, so it returns 0 without clearing anything [4](#0-3) .
- The claim then falls into `_claimFundedWithdrawRequest`. Its only epoch gate is `epochNumber <= lastWithdrawRequest[_user]` — which is false after the increment — and it pays `withdrawsRequests[_user]` at par via `_transferFundedClaim` [5](#0-4) .
- The guard meant to force claiming the loss-adjusted receipt before a new request (lines 263–271) suffers the same epoch-key mismatch, so it also cannot catch it.

### Impact Explanation
The strategy only pulled `_amount < pendingBasis` underlyings from the CDO, but every receipt holder in the haircut epoch can claim `withdrawsRequests[_user]` at 100%. Early claimers are overpaid relative to the realized loss (theft of the shortfall), and once the funded balance is exhausted, later claimants' `safeTransfer` reverts — their recovery is permanently frozen since no other path pays them. This breaks the loss-socialization invariant: the loss is borne entirely by whoever claims last rather than pro rata, and the aggregate insolvency equals `pendingBasis - _amount` (the full realized loss).

### Likelihood Explanation
Requires only a `stopEpochWithDuration`/`stopEpoch` where the borrower funds less than `pendingWithdraws` (a partial loss on pending receipts) — an honest-manager sequence the code explicitly supports. Any unprivileged receipt holder benefits by claiming early; no privileged collusion needed. Uncertainty: if the CDO calls `collectWithdrawFunds` *before* the `deposit()` that bumps `epochNumber`, the keying would be consistent; the ordering inside `IdleCDOEpochVariant._beforeStopEpoch`/`_afterStopEpochWithDuration` was not fully verifiable in this pass, but `deposit()`'s comment ("deposit done on stopEpoch … so we reset the counter") confirms the increment happens during stop-epoch processing, making the mismatch the consistent outcome.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch in which the receipts were created (i.e., `epochNumber - 1` after the bump, or capture `epochNumber` before `deposit()` increments it), or store the pending-receipt epoch explicitly when funding. Also fix the same keying in the `requestWithdraw` re-request guard so it derives the loss epoch from `withdrawsRequestsByEpoch` entries rather than relying solely on `lastWithdrawRequest`.

### Proof of Concept
```solidity
// Fork test against mainnet pool (IdleCreditVault + IdleCDOEpochVariant):
// 1. user A and user B depositAA; startEpoch; both requestWithdraw(tranche bal).
// 2. warp to epoch end; borrower repays only half of pendingWithdraws;
//    manager calls stopEpochWithDuration(_lossAmount) such that
//    collectWithdrawFunds is called with _amount < pendingWithdraws.
//    Assert lossRecoveryPriceByEpoch[strategy.epochNumber()] != 0 (post-bump key).
// 3. user A calls cdoEpoch.claimWithdrawRequest():
//    - _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch
//      [lastWithdrawRequest[A]] == 0 and skips the haircut.
//    - _claimFundedWithdrawRequest passes the epochNumber > lastWithdrawRequest
//      gate and pays A the full withdrawsRequests[A].
//    assertEq(underlying.balanceOf(A) - balPre, requestedA); // par, not haircut
// 4. user B calls claimWithdrawRequest(): safeTransfer reverts / pays less,
//    despite B being owed the same pro-rata haircut amount.
//    assertEq(lossRecoveryPriceByEpoch[strategy.lastWithdrawRequest(B)], 0);
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-349)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-795)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;
```
