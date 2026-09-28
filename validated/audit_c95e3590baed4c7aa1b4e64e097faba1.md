### Title
`collectWithdrawFunds` records the loss-recovery price under the wrong epoch index, so loss-adjusted receipts claim at par and drain other users' funded claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The `stszin` bug class is an index/lookup mismatch: a value is written to a table slot that the reader never consults. The analog is the epoch-keyed `lossRecoveryPriceByEpoch` mapping in `IdleCreditVault`. Withdrawal receipts record their haircut epoch via `lastWithdrawRequest[_user] = epochNumber` at request time, but `epochNumber` is incremented inside `deposit()` when the CDO pulls funds during `stopEpoch` (it increments whenever `isEpochRunning()` is still true). When `stopEpochWithDuration` applies a partial loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` using the already-incremented epoch, while victims' receipts point at the pre-increment epoch.

### Finding Description
- `requestWithdraw` saves `lastWithdrawRequest[_user] = currentEpoch` and the per-epoch basis in `withdrawsRequestsByEpoch[_user][currentEpoch]`. [1](#0-0) 
- `deposit()` increments `epochNumber` while the epoch is still flagged running — i.e., inside the `stopEpoch` repayment flow, before `collectWithdrawFunds` runs. [2](#0-1) 
- `collectWithdrawFunds` writes the haircut under `lossRecoveryPriceByEpoch[epochNumber]` — the post-increment value. [3](#0-2) 
- `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the pre-increment epoch — finds `0`, and returns early. [4](#0-3) 
- The claim then falls through to `_claimFundedWithdrawRequest`, which pays the full un-haircut `withdrawsRequests[_user]` basis once `epochNumber > lastWithdrawRequest[_user]`. [5](#0-4) 
- The guard in `requestWithdraw` that would force users to claim loss-adjusted receipts first reads the same (empty) slot, so it never triggers either. [6](#0-5) 

Broken invariant: the loss waterfall. Pending receipts escape the `stopEpochWithDuration` haircut entirely; only `pendingToFund` (the reduced amount) was pulled into the strategy, yet receipts redeem at 100%.

### Impact Explanation
After a realized loss, each pending-withdraw receipt pays out its full basis instead of `basis * lossRecoveryPrice`. The deficit is covered by underlying held in the strategy that backs other participants — funded claims of other users, reserved default recovery, or active strategy-token value — so early claimers steal the haircut difference from later claimers and LPs, and the pool becomes insolvent for the remainder. Loss is `pendingBasis - amountFunded`, quantified by the shortfall between aggregate claim basis and collected funds.

### Likelihood Explanation
Requires only an unprivileged tranche holder who files a normal withdraw request before an epoch in which the manager calls `stopEpochWithDuration`/`stopEpoch` with a realized loss, then calls `claimWithdrawRequest` after the epoch rolls. The attacker needs no privilege; the loss path is a normal protocol mode. One caveat: this finding depends on `collectWithdrawFunds` being invoked after `deposit()` inside the CDO stop flow — I was unable to fully verify the call ordering in `IdleCDOEpochVariant.stopEpoch` within the search budget; if the collect call precedes the deposit, the epoch index matches and the issue does not manifest.

### Recommendation
Store the loss under the epoch that pending receipts were recorded against — e.g., capture `epochNumber` before any increment (or have `collectWithdrawFunds` take the claim epoch as a parameter from the CDO), and add an invariant test asserting `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0` for every receipt that existed at loss time.

### Proof of Concept
Foundry fork sketch:

```solidity
// setup: users deposit AA, epoch N starts
// 1. victim + attacker call cdoEpoch.requestWithdraw(...) -> lastWithdrawRequest = N
// 2. borrower repays only partial funds; manager calls
//    cdoEpoch.stopEpochWithDuration(loss) / stopEpoch(apr, loss)
//    -> strategy.deposit() bumps epochNumber to N+1
//    -> collectWithdrawFunds(funded < pending) writes lossRecoveryPriceByEpoch[N+1]
// assert(strategy.lossRecoveryPriceByEpoch(N) == 0);      // lookup slot for receipts
// assert(strategy.lossRecoveryPriceByEpoch(N + 1) != 0);  // orphaned haircut
// 3. attacker calls cdoEpoch.claimWithdrawRequest()
//    -> _claimLossAdjustedWithdrawRequest returns 0 (price[N] == 0)
//    -> _claimFundedWithdrawRequest pays full basis
// assertEq(received, fullBasis); // > funded haircut amount
// 4. subsequent claimers' funded claims revert on insufficient strategy balance,
//    or drain the default-recovery reserve.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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
