### Title
Loss haircut from `stopEpochWithDuration` is only applied to current-epoch receipts — older pending withdraw receipts claim at par and drain the underfunded pool - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`collectWithdrawFunds` computes a `lossRecoveryPrice` over the **aggregate** `pendingWithdraws` (all pending receipts, all epochs) but stores it under the **current** `epochNumber`. A user's loss-adjusted claim is only routed through `_claimLossAdjustedWithdrawRequest` when `lastWithdrawRequest[user]` equals that epoch. Users whose withdraw requests were made in an earlier epoch and never claimed (explicitly supported: they "wait another epoch to claim both") keep `lastWithdrawRequest` pointing at the old epoch, skip the haircut entirely, and are paid at par via `_claimFundedWithdrawRequest` — even though their basis was already included in the haircut pool. First claimants overdraw; later claimants' transactions revert on insufficient balance.

### Finding Description
- In `collectWithdrawFunds`, when the borrower under-funds pending withdraws, the haircut price is `pendingBasis`-wide but `lossRecoveryPriceByEpoch[epochNumber]` is epoch-scoped, and `pendingWithdraws` is zeroed [1](#0-0) .
- `previewLossAdjustedWithdrawFunds` confirms the loss is meant to be shared pro-rata by *all* pending receipts [2](#0-1) .
- `_claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]`; for a requester from an earlier epoch this is 0, so the claim falls through [3](#0-2) .
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[user]` (which still contains the old, supposedly-haircut amount) at par, gated only by `epochNumber > lastWithdrawRequest`, which is already satisfied [4](#0-3) .
- `requestWithdraw` only blocks re-requesting when the *user's own* `lastWithdrawRequest` epoch carries a loss price; it does not prevent holding stale-epoch receipts across a later loss epoch [5](#0-4) .

This is the credit-vault analog of the CVE's invalid free: a receipt already "consumed" into the loss-adjusted aggregate is freed/claimed a second time through the par-funded path.

### Impact Explanation
Attacker (any KYC-passed lender): requests withdraw in epoch N, intentionally does not claim. In epoch M>N, `stopEpochWithDuration` realizes a loss; the borrower's under-funding produces `lossRecoveryPrice = funded/pendingBasis` stored at epoch M. The attacker calls `claimWithdrawRequest` and receives 100% of their receipt at par from the strategy's funded balance, which only contains `lossRecoveryPrice * pendingBasis`. Each such claim steals the haircut share that belongs to epoch-M requesters; once drained, epoch-M claimants' `_transferFundedClaim` reverts → permanent freezing of their funds. Loss equals `claimBasis * (1 - lossRecoveryPrice)` per stale-epoch receipt, up to the entire unfunded remainder.

### Likelihood Explanation
Requires only: (1) a user holds an unclaimed withdraw receipt across an epoch boundary — normal, documented behavior; (2) a later `stopEpochWithDuration` loss partially funding `pendingWithdraws`. Both are reachable with honest privileged roles. The attacker's cost is one `requestWithdraw` and one `claimWithdrawRequest`; first-come ordering guarantees the theft.

### Recommendation
Tag every pending receipt with its effective claim epoch, or store a per-user (or global) list of loss epochs and iterate `lossRecoveryPriceByEpoch` for each outstanding `withdrawsRequestsByEpoch` entry at claim time. Alternatively, when `collectWithdrawFunds` applies a loss, iterate/migrate earlier-epoch receipts so `lastWithdrawRequest` or a per-epoch flag forces them through the haircut path, and revert funded-path claims while any uncleared loss-epoch receipt exists in `withdrawsRequests[user]`.

### Proof of Concept
```solidity
// Fork test on IdleCreditVault + IdleCDOEpochVariant (APR != 0 mode)
// 1) User A deposits via AA tranche, then cdoEpoch.requestWithdraw(x, AA) in epoch N.
// 2) User B deposits and requests withdraw in epoch M (after startEpoch/stopEpoch advance epochNumber).
//    Both receipts accumulate in pendingWithdraws.
// 3) In epoch M, borrower under-repays: manager calls stopEpochWithDuration(apr, 0, duration, loss)
//    such that collectWithdrawFunds(_amount < pendingBasis) stores
//    lossRecoveryPriceByEpoch[M] = _amount * 1e18 / pendingBasis and sets pendingWithdraws = 0.
// 4) A calls claimWithdrawRequest(A):
//      - lastWithdrawRequest[A] == N, lossRecoveryPriceByEpoch[N] == 0 -> skip loss path
//      - _claimFundedWithdrawRequest passes epochNumber(M+...) > N check
//      - pays withdrawsRequests[A] AT PAR from the underfunded balance.
// 5) B calls claimWithdrawRequest(B):
//      - pays claimBasis * lossRecoveryPrice via _transferFundedClaim -> reverts
//        ERC20: transfer amount exceeds balance  (or returns less than funded share).
// assert: A received full amount; B's claim reverts / receives less than
//         withdrawsRequestsByEpoch[B][M] * lossRecoveryPriceByEpoch[M] / 1e18.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L454-459)
```text
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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
