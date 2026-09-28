### Title
Old-epoch withdraw receipt escapes `stopEpochWithDuration` loss haircut, draining funded claims of other pending redeemers - ([contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
In `IdleCreditVault`, when a borrower-funded `stopEpoch` applies a loss to pending withdraw receipts, the haircut is computed over the *entire* `pendingWithdraws` aggregate, but only receipts recorded under the user's *latest* request epoch (`lastWithdrawRequest[_user]`) are actually haircutted at claim time. A user who holds an older, already-claimable receipt and opens a second request before claiming the first can later claim the old receipt **at par**, while the loss was already priced assuming it would share the haircut. This overdraws the funded pool and leaves later claimants underpaid.

### Finding Description
`requestWithdraw` mints the user a receipt and records the basis per epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]`, accumulates the global `pendingWithdraws`, and overwrites `lastWithdrawRequest[_user] = currentEpoch` [1](#0-0) .

When the borrower under-funds at epoch stop, `collectWithdrawFunds` computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` where `pendingBasis` is the full `pendingWithdraws` (all epochs' receipts), then zeroes `pendingWithdraws` [2](#0-1) . The same split is previewed by `previewLossAdjustedWithdrawFunds`, which spreads `_lossAmount` pro-rata over `activeBasis + pendingBasis` [3](#0-2) .

At claim time, `_claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and clears only `withdrawsRequestsByEpoch[_user][lossEpoch]` [4](#0-3) . The older epoch's basis remains in `withdrawsRequests[_user]` (it is decremented only for the claimed epoch in `_clearWithdrawClaimForEpoch`, and `lastWithdrawRequest` is reset to 0) [5](#0-4) . `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` — including the old receipt — at par [6](#0-5) . The `_transferFundedClaim` guard only protects `defaultRecoveryReserve`, not other pending claimants [7](#0-6) .

The existing guard at lines 263–271 only blocks a new request when a loss-adjusted price already exists for the user's last epoch; it does not prevent stacking a fresh request on top of an unclaimed funded receipt before the loss occurs.

### Impact Explanation
Direct theft / insolvency in the pending-receipt pool. Suppose user A has a claimable receipt of `X` requested in epoch N, then requests `Y` more in epoch N+1, and other users request `Z`. A stop loss produces `lossRecoveryPrice = funded / (X + Y + Z) < 1`. A's epoch-N receipt `X` was counted in the haircut basis but A redeems it at par (loss = `X * (1 - price)` socialized onto the `{Y, Z}` claimants, who already bear their own share). If A withdraws before the N+1 claimants, the strategy's funded balance is insufficient to pay them — last claimants' transactions revert or pay less than `claimBasis * price`. A rational user simply delays claiming and files a second request to guarantee their old receipt escapes any future stopEpoch loss, violating the "one receipt one payout" and loss-waterfall invariants.

### Likelihood Explanation
Requires no privileged action: any KYC'd lender can hold a receipt across an epoch boundary and re-request (a documented, intended flow — "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests"). It triggers whenever `stopEpochWithDuration`/`collectWithdrawFunds` realizes a loss while such stacked receipts exist — a normal (if infrequent) credit-vault event.

### Recommendation
Track per-epoch haircut coverage instead of a single `lastWithdrawRequest`. Either (a) apply `lossRecoveryPriceByEpoch` to *every* unclaimed epoch basis of the user (iterate/migrate `withdrawsRequestsByEpoch`), or (b) reduce the loss-adjusted funding math to only the basis actually haircutted, or (c) force claim/settlement of all prior receipts (not only loss-adjusted ones) before accepting a new `requestWithdraw`, extending the existing `NotAllowed` guard to any unclaimed receipt from an earlier epoch.

### Proof of Concept
Foundry fork PoC (scaffolded against `test/foundry/ProgrammableBorrowerCreditVault.t.sol` style):

```solidity
// Setup: vault with borrower, apr > 0, two users A (attacker) and B.
// 1) Epoch N running: A deposits AA, calls requestWithdraw(X_A). B deposits.
// 2) Manager stops epoch normally; borrower funds pendingWithdraws in full.
//    A's receipt is now claimable at par, but A does NOT claim.
// 3) Epoch N+1 starts. A calls requestWithdraw(Y_A) again — allowed because
//    lossRecoveryPriceByEpoch[lastWithdrawRequest[A] = N] == 0.
//    B calls requestWithdraw(Z_B). pendingWithdraws = X_A + Y_A + Z_B.
// 4) Owner calls stopEpochWithDuration(_lossAmount > 0): borrower funds only
//    pendingBasis - pendingLoss; collectWithdrawFunds stores
//    lossRecoveryPriceByEpoch[N+1] = funded/pendingBasis < 1.
// 5) A calls claimWithdrawRequest:
//    - _claimLossAdjustedWithdrawRequest pays Y_A * price (only epoch N+1 basis).
//    - _clearWithdrawClaimForEpoch sets lastWithdrawRequest[A] = 0.
//    - _claimFundedWithdrawRequest then pays withdrawsRequests[A] = X_A AT PAR.
//    Assert: A received X_A + Y_A*price  (should have been (X_A + Y_A) * price).
// 6) B calls claimWithdrawRequest: receives Z_B * price, but the strategy's
//    underlying balance is now funded - A_payout < total obligations;
//    assert B's payout < Z_B * price or the transfer reverts
//    (insolvency of X_A * (1 - price)).
```

Quantified loss to other claimants: `X_A * (1 - lossRecoveryPrice)`, directly stolen/socialized by the multi-epoch receipt holder.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L273-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
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
