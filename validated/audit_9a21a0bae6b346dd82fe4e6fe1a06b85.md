### Title
Loss-adjusted withdraw haircut is invalidated when a user re-requests withdrawal before claiming — receipt paid at par and drains other claimants' funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The LibreOffice CVE maps to the "stale receipt not invalidated" class: `IdleCreditVault` records a per-epoch loss haircut via `lossRecoveryPriceByEpoch`, but the lookup key for the haircutted claim is the mutable `lastWithdrawRequest[_user]`. A second `requestWithdraw` overwrites that pointer, permanently orphaning the haircutted epoch's entry in `withdrawsRequestsByEpoch` and causing the aggregate `withdrawsRequests[_user]` (including the haircutted basis) to be paid out at par through `_claimFundedWithdrawRequest`.

### Finding Description
- `requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` unconditionally on every call [1](#0-0) .
- When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` funds only `pendingToFund` and stores `lossRecoveryPriceByEpoch[epochNumber]` [2](#0-1) .
- The loss-adjusted payout path `_claimLossAdjustedWithdrawRequest` resolves the claim epoch *only* from `lastWithdrawRequest[_user]` [3](#0-2) .
- If the user calls `requestWithdraw` again in a later epoch before claiming, `lastWithdrawRequest` is overwritten, so `lossRecoveryPriceByEpoch[lossEpoch]` is never consulted for the old receipt and `withdrawsRequestsByEpoch[_user][oldEpoch]` is never cleared [4](#0-3) .
- `_claimFundedWithdrawRequest` then pays the full aggregate `withdrawsRequests[_user]` — which still includes the old, un-haircutted basis — at par, once `epochNumber > lastWithdrawRequest[_user]` [5](#0-4) .

The vault only ever received `pendingToFund < pendingBasis` for that epoch, so paying the stale basis at par spends underlyings that belong to other funded receipts (or reverts in `_transferFundedClaim` under the reserve check, freezing later claimants) [6](#0-5) .

### Impact Explanation
Direct theft / insolvency: an unprivileged lender escapes the epoch loss haircut entirely and is paid `claimBasis` instead of `claimBasis * lossRecoveryPrice / RECOVERY_FULL`. With e.g. a 30% haircut on a large receipt, the excess payout consumes funds reserved for other withdraw claimants; the last claimants either receive less or are permanently frozen by the `balance - reserve < _amount` guard. Any KYC-passing lender can trigger this with two ordinary `requestWithdraw` calls around a loss epoch — no privileged action needed.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss epoch (honest borrower partially repaying) and the attacker re-requesting a withdrawal in a subsequent epoch — both normal, unprivileged user flows. No attacker influence over the loss is needed; they simply avoid claiming until a new epoch opens and re-request a dust amount. The code path is deterministic once a loss-adjusted epoch exists.

### Recommendation
Track loss-adjusted claims per epoch rather than via the single `lastWithdrawRequest` pointer: e.g. in `_claimLossAdjustedWithdrawRequest`, iterate or store the user's haircutted epochs, or prevent `requestWithdraw` from overwriting `lastWithdrawRequest` while `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` (settle the pending loss-adjusted claim inside `requestWithdraw` first). At minimum, `_claimFundedWithdrawRequest` should exclude epochs with a non-zero `lossRecoveryPriceByEpoch`.

### Proof of Concept
Foundry fork PoC outline:

```solidity
// setup: deposit as user A (attacker) and user B into AA tranche
// epoch N: A requests withdraw (receipt R_A recorded in withdrawsRequestsByEpoch[A][N])
// manager calls stopEpochWithDuration(lossAmount) such that
//   collectWithdrawFunds funds only pendingToFund and sets
//   lossRecoveryPriceByEpoch[N] = 0.7e18
// epoch N+1 (running): A calls cdoEpoch.requestWithdraw(dust, AAtranche)
//   -> lastWithdrawRequest[A] overwritten to N+1
//   -> withdrawsRequestsByEpoch[A][N] never cleared, still inside withdrawsRequests[A]
// epoch N+2 after stopEpoch: A calls cdoEpoch.claimWithdrawRequest()
//   -> _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[N+1] == 0, returns 0
//   -> _claimFundedWithdrawRequest pays withdrawsRequests[A] (full basis of epoch N) at par
// assert: A received R_A instead of R_A * 0.7e18 / 1e18
// assert: vault balance now insufficient for B's funded claim (or B's claim reverts NotAllowed)
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-425)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-836)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
