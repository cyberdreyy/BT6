### Title
Multi-epoch withdraw receipts escape `stopEpochWithDuration` loss haircut via single `lastWithdrawRequest` pointer - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` computes a single pro-rata `lossRecoveryPrice` against the *entire* `pendingWithdraws` basis, but the per-epoch receipt clearing in `_claimLossAdjustedWithdrawRequest`/`_clearWithdrawClaimForEpoch` only looks up the epoch stored in `lastWithdrawRequest[user]`. A user holding withdraw receipts recorded under earlier epochs (in `withdrawsRequestsByEpoch[user][olderEpoch]`) has those slices left in the aggregate `withdrawsRequests[user]` and is paid them **at par** through `_claimFundedWithdrawRequest`, even though they were counted in the denominator of the haircut. This mirrors the reported bug class: a fixed assumption of "one receipt per pointer/length" is violated by multi-epoch data, and the mismatch silently pays the full length rather than reverting or prorating consistently.

### Finding Description
- `requestWithdraw` records receipts per epoch (`withdrawsRequestsByEpoch[_user][currentEpoch]`) but keeps only one marker, `lastWithdrawRequest[_user] = currentEpoch` [1](#0-0) .
- On a partial borrower repayment, `collectWithdrawFunds` sets `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` where `pendingBasis` includes *all* outstanding receipts across epochs [2](#0-1) .
- `_claimLossAdjustedWithdrawRequest` haircuts only `withdrawsRequestsByEpoch[user][lastWithdrawRequest[user]]` [3](#0-2) ; `_clearWithdrawClaimForEpoch` subtracts only that epoch's slice from `withdrawsRequests[user]` [4](#0-3) .
- `_claimFundedWithdrawRequest` then pays the remaining aggregate `withdrawsRequests[user]` — including older-epoch slices — at par [5](#0-4) .
- The re-request guard in `requestWithdraw` only reverts when a *priced* epoch is already stored at `lastWithdrawRequest`; a request made in a later epoch before the loss occurs is unrestricted [6](#0-5) .

### Impact Explanation
Broken invariant: fair loss socialization / one-receipt-one-pricing. When `collectWithdrawFunds` receives less than `pendingBasis`, every pending receipt should bear the same haircut. Instead, an attacker with receipts spanning two epochs is paid `olderSlice * 1.0 + latestSlice * lossRecoveryPrice`, extracting more than their pro-rata share from the funded amount. Since the funded `_amount` was sized assuming all receipts take the haircut, honest claimants' funded claims are correspondingly under-collateralized — the last claimants can revert or be shortchanged (direct theft up to `olderSlice * (1 - lossRecoveryPrice)` per multi-epoch receipt).

### Likelihood Explanation
Requires only unprivileged actions: two `requestWithdraw` calls (via the CDO as a tranche holder) in consecutive epochs, sequenced before an honest `stopEpochWithDuration`/`collectWithdrawFunds` that funds receipts partially. No privileged misbehavior needed — the loss path is a normal protocol flow. Constraint: the older receipt must remain unclaimed, which is natural since claims are blocked while `epochNumber <= lastWithdrawRequest` until an epoch boundary passes.

### Recommendation
Apply the haircut to the user's *entire* pending basis, not just the slice at `lastWithdrawRequest`: either iterate/clear all `withdrawsRequestsByEpoch` entries for the user, or store a per-user aggregate pending basis that `_claimLossAdjustedWithdrawRequest` clears atomically. Alternatively, revert (like the dynamic→static length mismatch recommendation) when a user has receipt basis in epochs other than `lastWithdrawRequest`, forcing them to claim each epoch's receipt separately so pricing stays consistent.

### Proof of Concept
```solidity
// Foundry fork PoC (pseudocode against deployed vault/CDO)
function test_multiEpochReceiptEscapesHaircut() public {
    // Epoch N: attacker requests withdraw of 100 tranche tokens
    cdo.withdraw(100e18); // -> strategy.requestWithdraw: lastWithdrawRequest=N, byEpoch[N]=100
    stopEpoch();          // epochNumber becomes N+1, receipt funded... leave unclaimed

    // Epoch N+1: attacker requests another 100
    cdo.withdraw(100e18); // lastWithdrawRequest=N+1, byEpoch[N+1]=100
    // Honest user also has 100 pending in epoch N+1; pendingWithdraws = 300 total
    // (attacker 200 + honest 100)

    // Borrower repays only 150 -> lossRecoveryPrice = 150/300 = 0.5e18
    borrower.partialRepay(150e18);
    cdo.stopEpochWithDuration(...);

    // Attacker claim:
    //  _claimLossAdjustedWithdrawRequest: byEpoch[N+1]=100 -> pays 100*0.5 = 50
    //  _claimFundedWithdrawRequest: withdrawsRequests still has byEpoch[N]=100 -> pays 100 at par
    //  attacker receives 150 total (50% haircut on only half his basis)
    // Honest user receives 100*0.5 = 50.
    // Total paid 200 > funded 150 -> last claim underfunded / reserve drained.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-293)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-420)
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
