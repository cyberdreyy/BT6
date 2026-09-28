### Title
`IdleCreditVault` loss-adjusted claim clears only the epoch keyed by `lastWithdrawRequest`, orphaning earlier-epoch receipt basis that is later paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Nouns `updateFounders` bug is a stale-state cleanup failure: the code reconstructs which `tokenRecipient` slots to delete using a schedule offset (`baseTokenId = 0`) that ignores `reservedUntilTokenId`, so old founder allocations survive the "clearing" pass and keep receiving mints. The same pattern exists in `IdleCreditVault._claimLossAdjustedWithdrawRequest` / `_clearWithdrawClaimForEpoch`: when a loss-adjusted payout is claimed, the strategy only clears the single epoch stored in `lastWithdrawRequest[_user]`, even though a user's receipt basis can be spread across multiple epochs in `withdrawsRequestsByEpoch` and `withdrawsRequests` (which is an aggregate). The orphaned earlier-epoch basis is then paid a second time, at par, through `_claimFundedWithdrawRequest`, defeating the loss haircut.

### Finding Description
`requestWithdraw` supports a user holding receipts from multiple epochs: a user who does not claim can request again, and the code records basis per epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]` while overwriting `lastWithdrawRequest[_user] = currentEpoch` [1](#0-0) . The comment in `_claimFundedWithdrawRequest` explicitly acknowledges this: "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests" [2](#0-1) .

When `stopEpochWithDuration` funds pending receipts with a loss haircut, the recovery ratio is stored in `lossRecoveryPriceByEpoch[epoch]` for a single epoch key. On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and calls `_clearWithdrawClaimForEpoch(_user, lossEpoch, false)`, which zeroes only `withdrawsRequestsByEpoch[_user][lossEpoch]` and subtracts only that epoch's `normalAmount` from the aggregate `withdrawsRequests[_user]` [3](#0-2) [4](#0-3) .

This is exactly the `updateFounders` defect: the cleanup pass reconstructs the set of entries to clear from a single index ("offset") — `lastWithdrawRequest` — that does not cover the full allocation. If the same user has basis in an earlier request epoch `N` and a later epoch `M`, and the loss price is recorded under `M`, only the `M` basis is haircut and burned. The `N` basis remains inside `withdrawsRequests[_user]` (only `normalAmount` for epoch `M` is subtracted at line 819) and in `withdrawsRequestsByEpoch[_user][N]`. Execution then falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` — still containing the epoch-`N` amount — in full via `_transferFundedClaim` [5](#0-4) . The guard added in `requestWithdraw` (lines 263–271) repeats the same flaw: it checks only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, so a request in a new epoch overwrites the marker and lets a user bypass the "claim your loss-adjusted receipt first" check while the older basis persists.

### Impact Explanation
The `lossRecoveryPriceByEpoch` haircut is the mechanism that socializes a `stopEpochWithDuration(_lossAmount)` loss across all pending receipts. A user holding multi-epoch receipts receives `claimBasis(M) * lossPrice + basis(N) * 1.0`, i.e. the epoch-`N` portion escapes the loss waterfall entirely and is paid at par from underlyings that were funded only up to the aggregate haircut. This overpayment is a direct wealth transfer from other claimants/tranche holders: the strategy's funded balance is drained beyond the intended recovery share, breaking the "one receipt, one haircut-adjusted payout" and loss-socialization invariants — the analog of the Nouns "hidden founders exceed totalOwnership" invariant violation.

### Likelihood Explanation
Requires a user to hold withdraw receipts in two distinct strategy epochs at the moment a loss-adjusted `stopEpochWithDuration` records `lossRecoveryPriceByEpoch` under the later epoch. This is a normal, permissionless usage pattern (request, don't claim, request again next epoch) — no privileged cooperation is needed beyond the honest manager executing a loss stop. The attacker is an ordinary KYC'd tranche holder.

### Recommendation
Mirror the report's fix: instead of keying the loss claim to a single `lastWithdrawRequest`, iterate/clear all epoch basis for the user (or restrict `lossRecoveryPriceByEpoch` claims to the exact epochs whose basis is included), and subtract the full per-user basis from `withdrawsRequests`. Additionally, the `requestWithdraw` guard at lines 263–271 should check all epochs with non-zero `withdrawsRequestsByEpoch[_user]` for a pending loss price, not only `lastWithdrawRequest[_user]`. Alternatively, enforce a single open request epoch per user (revert if an unclaimed earlier-epoch receipt exists), which collapses the schedule to one key.

### Proof of Concept
Foundry sketch (mock-based, per repo harness style):

```solidity
// Setup: user requests withdraw in epoch N (requestWithdraw -> withdrawsRequestsByEpoch[user][N] = a)
// epoch N stops, epoch N+1 buffer: user requests again -> withdrawsRequestsByEpoch[user][N+1] = b,
//        lastWithdrawRequest[user] = N+1
// Borrower repays with loss: cdoEpoch.stopEpochWithDuration(apr, interest, duration, lossAmount)
//        -> lossRecoveryPriceByEpoch[N+1] = p < 1e18, pendingWithdraws funded at haircut(a+b)
// Attack:
cdo.claimWithdrawRequest(user);
// _claimLossAdjustedWithdrawRequest: clears epoch N+1 only, pays b*p
// _claimFundedWithdrawRequest:    pays withdrawsRequests[user] == a (epoch-N remnant) at par
uint256 expected = (a + b) * p / 1e18;
uint256 actual   = b * p / 1e18 + a;
assertGt(actual, expected); // user escaped the haircut on `a`
```

**Uncertainty caveat:** I verified the claim-side clearing logic reads only `lastWithdrawRequest`, but I could not confirm within the available context the exact line where `lossRecoveryPriceByEpoch` is written (in the loss-funding path after `previewLossAdjustedWithdrawFunds`) and whether it is always keyed to the same epoch as the requester's `lastWithdrawRequest`. If the loss price is keyed to the strategy epoch at `stopEpoch` time while multi-epoch basis exists under older keys, the orphaning described above holds; if accounting forces all basis into the funded epoch key, this reduces to a non-issue. A full PoC in a Devin session should confirm the write site of `lossRecoveryPriceByEpoch`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-328)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L331-349)
```text
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
