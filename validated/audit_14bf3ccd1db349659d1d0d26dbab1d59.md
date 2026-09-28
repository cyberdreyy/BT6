### Title
Loss-adjusted withdraw receipts from earlier epochs are paid at par: `claimWithdrawRequest` only applies the haircut to `lastWithdrawRequest[_user]` epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The analog of the report's "claim doesn't validate/subtract the correct per-policy coverage" is `IdleCreditVault.claimWithdrawRequest`: when the borrower under-funds pending withdraws at `stopEpochWithDuration`, the haircut is recorded per epoch in `lossRecoveryPriceByEpoch`, but the claim path only applies the haircut to the epoch stored in `lastWithdrawRequest[_user]` — a single slot overwritten on every `requestWithdraw`. Any loss-adjusted receipt from an earlier epoch falls through to `_claimFundedWithdrawRequest`, which pays the aggregate `withdrawsRequests[_user]` at par.

### Finding Description
`collectWithdrawFunds` records a per-epoch recovery price when the borrower funds less than `pendingWithdraws`: `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` [1](#0-0) . Each `requestWithdraw` overwrites `lastWithdrawRequest[_user] = currentEpoch` [2](#0-1) .

On claim, `_claimLossAdjustedWithdrawRequest` reads only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, clears that single epoch via `_clearWithdrawClaimForEpoch` (which subtracts only that epoch's amount from `withdrawsRequests[_user]`) and sets `lastWithdrawRequest[_user] = 0` [3](#0-2) [4](#0-3) . `_claimFundedWithdrawRequest` then pays the remaining `withdrawsRequests[_user]` balance — which still contains the earlier loss-adjusted epoch's full basis — at par, with the epoch-wait gate trivially passing since `lastWithdrawRequest` was just zeroed [5](#0-4) . Nothing re-checks `lossRecoveryPriceByEpoch[M]` for the older epoch M.

### Impact Explanation
Broken invariant: loss socialization / one receipt one (haircutted) payout. The strategy only collected `claimBasis_M * lossRecoveryPrice_M / RECOVERY_FULL` for epoch-M receipts, but pays `claimBasis_M`. Attacker over-claims `claimBasis_M * (1 - lossRecoveryPrice_M)`, directly draining underlyings that back other pending/funded claimants or recovery reserve — quantified theft equal to the unpaid haircut, e.g. a 10 ETH basis at 70% recovery steals 3 ETH.

### Likelihood Explanation
Unprivileged path: any KYC'd lender requests withdraws in two distinct epochs where the honest borrower under-funds twice (a `stopEpochWithDuration` shortfall each time — no default required, no privileged misbehavior). The attacker then makes a single `claimWithdrawRequest`. Guards do not stop it: the epoch gate is cleared, per-epoch subtraction is explicit but scoped only to `lastWithdrawRequest`, and there is no check that remaining `withdrawsRequests[_user]` entries belong to fully-funded epochs.

### Recommendation
In `claimWithdrawRequest`/`_claimFundedWithdrawRequest`, iterate or track all epochs with a non-zero `withdrawsRequestsByEpoch[_user][epoch]` (e.g. keep a per-user list of request epochs) and apply `lossRecoveryPriceByEpoch[epoch]` to each; only pay at par epochs whose `lossRecoveryPriceByEpoch` is zero. Alternatively, store the user's earliest unsettled loss epoch alongside `lastWithdrawRequest` and revert/re-route claims until it is cleared.

### Proof of Concept
Foundry fork sketch (patterns from `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Epoch M: user deposits AA and requests withdraw of 10_000e18
_depositWithUser(user, 10_000 * ONE_SCALE, true);
vm.prank(user);
uint256 basisM = cdoEpoch.requestWithdraw(0, address(AAtranche));
// stopEpoch with borrower under-funding: fund only 70% of pendingWithdraws
// (stopEpochWithDuration loss path -> collectWithdrawFunds(0.7*basisM))
// => lossRecoveryPriceByEpoch[M] = 0.7e18, pendingWithdraws = 0

// Epoch N: user deposits again and requests withdraw of 5_000e18
// overwrites lastWithdrawRequest[user] = N
// second under-funded stop -> lossRecoveryPriceByEpoch[N] = 0.7e18

vm.prank(user);
cdoEpoch.claimWithdrawRequest();
// Observed: epoch N paid at 0.7x (correct), epoch M's 10_000e18 paid at PAR
// via _claimFundedWithdrawRequest => strategy pays 3_000e18 more than collected,
// reducing balance available to other claimants / reserve.
// Assert: underlyingReceived == 0.7*basisN + basisM  (expected: 0.7*(basisM+basisN))
```

Key assertions: `lossRecoveryPriceByEpoch[M] != 0` while the payout for epoch-M basis equals `basisM`, and `strategy.underlying balance` shortfall equals `basisM * (RECOVERY_FULL - 0.7e18)/RECOVERY_FULL`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-421)
```text
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L815-836)
```text
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
