### Title
Loss-adjusted withdraw receipts bypass their haircut when a newer request overwrites `lastWithdrawRequest` — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimWithdrawRequest` decides whether a user has a haircutted receipt by looking up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — it inspects only the **most recent** request epoch (the "immediate parent"), not every epoch in which the user holds a receipt. If an attacker holds a receipt from a loss-adjusted epoch and then files a new withdraw request in a later epoch, `lastWithdrawRequest` is overwritten, the loss-adjusted claim path is skipped, and `_claimFundedWithdrawRequest` pays the **entire aggregate** `withdrawsRequests[_user]` — including the haircutted basis — at par. This is the direct analog of the Sparkle bug: the parent-symlink check inspects only one path component, so an attacker-planted "symlink" (a stale loss-epoch receipt hidden behind a newer request) escapes the guard and the write/payout lands outside the intended boundary.

### Finding Description
In `IdleCreditVault.claimWithdrawRequest` the dispatch order is:

1. `_claimLossAdjustedWithdrawRequest(_user)` uses `lastWithdrawRequest[_user]` as the **only** epoch key into `lossRecoveryPriceByEpoch` [1](#0-0) .
2. `_claimFundedWithdrawRequest(_user)` then pays `withdrawsRequests[_user]` — the aggregate across **all** epochs — at par once `epochNumber > lastWithdrawRequest[_user]` [2](#0-1) .

`requestWithdraw` accumulates per-epoch (`withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`) but only stores a single epoch in `lastWithdrawRequest[_user] = currentEpoch` [3](#0-2) .

When a stop funds fewer underlyings than `pendingWithdraws`, `collectWithdrawFunds` stores the haircut in `lossRecoveryPriceByEpoch[epochNumber]` and clears `pendingWithdraws` — but per-user `withdrawsRequests`/`withdrawsRequestsByEpoch` entries are **not** reduced; they are only cleared lazily when that epoch's receipt is claimed through the loss-adjusted path [4](#0-3) .

Sequence (attacker = ordinary KYC'd lender):

- **Epoch 0 (buffer):** attacker deposits and calls `requestWithdraw` → `withdrawsRequests[A] = X`, `lastWithdrawRequest[A] = 0`.
- **Epoch 0 (stop, loss):** `stopEpochWithDuration` underfunds pending receipts; `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[0] = p < RECOVERY_FULL`. Attacker's claim is now entitled to only `X * p / RECOVERY_FULL`, and the haircut was already socialized into the loss split via `previewLossAdjustedWithdrawFunds` [5](#0-4) .
- **Epoch 1 (buffer):** attacker makes a dust deposit and a dust `requestWithdraw` → `lastWithdrawRequest[A] = 1`, `withdrawsRequests[A] = X + dust`.
- **After epoch 1 stop:** `epochNumber = 2 > lastWithdrawRequest = 1` → the funded-claim gate passes. `_claimLossAdjustedWithdrawRequest` checks `lossRecoveryPriceByEpoch[1] == 0` and returns 0 without clearing the epoch-0 basis. `_claimFundedWithdrawRequest` then pays `X + dust` **at par** and burns the receipts.

The attacker recovers the haircut `X * (1 - p)` that the waterfall already assigned to pending receipts, paid out of underlyings the strategy holds for other funded claimants (or, post-finalization, guarded only by the `defaultRecoveryReserve` check in `_transferFundedClaim`, which does not account for this overpayment) [6](#0-5) .

`_clearWithdrawClaimForEpoch` shows the contract *does* track per-epoch ownership — the bug is purely that the claim entry point consults only the latest epoch rather than iterating all epochs with non-zero `withdrawsRequestsByEpoch[_user]` or `lossRecoveryPriceByEpoch` entries [7](#0-6) .

### Impact Explanation
Direct theft of `receiptBasis * (1 - lossRecoveryPrice)` per affected receipt. Because the loss haircut was already deducted from what the borrower had to fund at `stopEpoch`, paying the same receipt at par means the strategy pays out underlyings that were never collected for that receipt — effectively spending other claimants' funded balance. Quantified: with a 30% haircut on a 1,000,000 USDC receipt, the attacker extracts an extra 300,000 USDC by spending a dust re-request.

### Likelihood Explanation
Requires a loss-adjusted epoch (`collectWithdrawFunds` with `_amount < pendingBasis`, i.e. `stopEpochWithDuration` partial funding) followed by the attacker re-requesting in a later epoch — both are normal, unprivileged-lender-reachable flows; the loss event is produced by the honest manager/borrower path, so the attacker only sequences around it. No privileged role, no oracle, no reentrancy needed. The attacker must hold the tranche tokens through one extra epoch, which is the normal withdrawal lifecycle anyway.

### Recommendation
In `claimWithdrawRequest` / `_claimLossAdjustedWithdrawRequest`, do not key the loss lookup solely on `lastWithdrawRequest[_user]`. Either iterate all epochs where `withdrawsRequestsByEpoch[_user][e] != 0` and `lossRecoveryPriceByEpoch[e] != 0` (walk every "path component", not just the immediate parent), or maintain a per-user bitmap/set of loss-adjusted epochs, or at request time force-settle/clear any pre-existing loss-adjusted receipts before allowing a new request to overwrite `lastWithdrawRequest`. A cheaper invariant: in `requestWithdraw`, revert (or auto-claim) if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and that epoch's basis is uncleared.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossReceiptPaidAtParAfterNewRequest() external {
    address attacker = makeAddr('attacker');
    uint256 amount = 1_000_000 * ONE_SCALE;

    // Epoch 0: attacker deposits AA and requests withdraw
    _depositWithUser(attacker, amount, true);
    vm.prank(attacker);
    uint256 basis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(0);
    // stopEpochWithDuration with a loss -> collectWithdrawFunds funds < pendingBasis
    // lossRecoveryPriceByEpoch[0] = p < RECOVERY_FULL
    _stopEpochWithLoss(0, /* e.g. 70% funded */);
    uint256 price = IdleCreditVault(address(strategy)).lossRecoveryPriceByEpoch(0);
    assertLt(price, RECOVERY_FULL);

    // Epoch 1: attacker deposits dust and re-requests -> lastWithdrawRequest = 1
    uint256 dust = 1 * ONE_SCALE;
    _depositWithUser(attacker, dust, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertEq(IdleCreditVault(address(strategy)).lastWithdrawRequest(attacker), 1);

    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Claim: loss path keys on epoch 1 (price 0, skipped); funded path pays full aggregate
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;

    // Attacker is paid basis + dust at par instead of basis * price / FULL + dust
    assertGt(paid, basis * price / RECOVERY_FULL + dust);
}
```

Expected result: `paid ≈ basis + dust` while the strategy only collected `basis * price / RECOVERY_FULL` for the epoch-0 receipt — the difference is drained from underlyings backing other funded claims.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L454-460)
```text
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
  }
```
