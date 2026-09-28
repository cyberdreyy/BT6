### Title
Loss-adjusted withdraw receipts are keyed to the mutable `lastWithdrawRequest` epoch pointer, so a new request orphans the haircut and the old receipt is replayed at par — (`File: contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
Analogous to the missing chain-id validation (a transaction replayed in the wrong network context because the contextual identifier was never checked), `IdleCreditVault._claimLossAdjustedWithdrawRequest` does not validate *which* epoch a pending receipt belongs to. It looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, but `lastWithdrawRequest` is overwritten on every new `requestWithdraw`. A haircut receipt from a loss-adjusted epoch can therefore be silently reclassified as an ordinary funded receipt and paid at par, even though the borrower only funded `basis * lossRecoveryPrice` for it.

### Finding Description
- `requestWithdraw` records `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, and adds to the aggregate `withdrawsRequests[_user]` [1](#0-0) . Stacking a second request while one is open is explicitly supported pre-default ("he will have to wait for another epoch to claim both requests") [2](#0-1) .
- On a loss epoch, `collectWithdrawFunds` funds only `pendingBasis * lossRecoveryPrice`, zeroes `pendingWithdraws`, and stores `lossRecoveryPriceByEpoch[epochNumber]` [3](#0-2) .
- The recovery claim resolves the epoch solely via `lastWithdrawRequest[_user]` [4](#0-3) . `_clearWithdrawClaimForEpoch` then clears only `withdrawsRequestsByEpoch[_user][lossEpoch]`; any other epoch's basis remains inside `withdrawsRequests[_user]` [5](#0-4) .
- `_claimFundedWithdrawRequest` pays the full aggregate `withdrawsRequests[_user]` (plus APR0 buckets) at par once `epochNumber > lastWithdrawRequest[_user]` [6](#0-5) .

Attack sequence (attacker = ordinary KYC'd LP):
1. Epoch N buffer: attacker calls `requestWithdraw(A)` → `lastWithdrawRequest=N`, basis `A`.
2. Honest manager stops epoch N with a partial loss (`stopEpochWithDuration(..., _lossAmount>0)`); borrower funds `A·p` (`p = lossRecoveryPriceByEpoch`), `epochNumber` bumps to N+1.
3. Epoch N+1 buffer: attacker calls `requestWithdraw(B)` → `lastWithdrawRequest` is overwritten to N+1; `withdrawsRequests = A + B`.
4. Epoch N+1 stops healthy; borrower funds `B`; `epochNumber` bumps to N+2.
5. Attacker calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[N+1] == 0` and returns 0 — the epoch-N haircut never applies. `_claimFundedWithdrawRequest` then pays `A + B` at par and burns the attacker's `A + B` receipts.

The vault only received `A·p + B` in funding for those receipts, so the extra `A·(1−p)` is paid out of funded reserves belonging to other claimants.

### Impact Explanation
Direct theft / insolvency. The loss-waterfall invariant ("a stopEpoch loss is shared pro rata by pending receipts") is broken: the attacker escapes their `A·(1−p)` haircut and is made whole at par by consuming other users' funded withdraw proceeds (or the default recovery reserve later, since `_transferFundedClaim` draws on vault balance). Quantified loss to the vault/other claimants: `A·(1 − lossRecoveryPrice)`, which approaches `A` for severe losses.

### Likelihood Explanation
Requires an honest `stopEpochWithDuration` with `_lossAmount > 0` (a documented, non-default loss path, e.g. partial borrower shortfall) and one additional `requestWithdraw` by the attacker in the following buffer — both ordinary unprivileged actions that sequence around honest manager calls. No privileged misbehavior needed. Note a residual uncertainty I could not fully confirm within the search limits: if `epochNumber` is incremented before `collectWithdrawFunds` runs inside `_stopEpoch`, `lossRecoveryPriceByEpoch` may be stored under the post-increment epoch while receipts were recorded under the pre-increment epoch — in which case the haircut is *never* applied even without step 3, making the bug stronger. Either indexing alignment leaves the haircut receipt payable at par because the claim path never validates the receipt's own epoch.

### Recommendation
Track the loss-adjusted basis per epoch and clear it against `withdrawsRequestsByEpoch[_user][epoch]` for *every* epoch the user has a receipt, not just `lastWithdrawRequest`. Concretely: iterate or record per-epoch loss flags (e.g., store `lossRecoveryPriceByEpoch` keyed by the same epoch used in `withdrawsRequestsByEpoch`, and in `claimWithdrawRequest` apply the haircut to each per-epoch basis before falling through to the par claim), mirroring how `defaultRecoveryEpoch` is validated against per-epoch balances. Alternatively, revert `requestWithdraw` when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` so an unclaimed haircut receipt must be claimed first (the same guard already applied post-default).

### Proof of Concept
```solidity
// Fork test in test/foundry/IdleCreditVault.t.sol style.
// Attacker = KYC'd AA holder; manager/owner/borrower act honestly.
function testLossReceiptPaidAtParAfterNewRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);      // attacker deposit

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // 1) attacker requests withdraw A in buffer (epoch recorded = N)
    uint256 reqA = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // 2) honest stop with realized loss -> borrower funds only reqA * p
    _startEpochAndCheckPrices(1);
    // stopEpochWithDuration(_newApr, _interest, _duration, _lossAmount > 0)
    // -> strategy.collectWithdrawFunds(A * lossPrice) sets lossRecoveryPriceByEpoch
    uint256 p = /* funded / pendingBasis */;

    // 3) attacker stacks a second request in the new buffer -> lastWithdrawRequest overwritten
    uint256 reqB = cdoEpoch.requestWithdraw(mintedAA / 4, address(AAtranche));

    // 4) healthy stop fully funds reqB; epochNumber advances past lastWithdrawRequest
    _startEpochAndCheckPrices(2);
    _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch());

    // 5) claim pays reqA + reqB at par; expected value was reqA*p + reqB
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(address(this)) - balPre;

    assertEq(paid, reqA + reqB);                 // haircut skipped
    // Excess reqA*(1 - p) is drained from other claimants' funded reserves.
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L320-324)
```text
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
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
