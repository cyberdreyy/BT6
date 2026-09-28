### Title
Stop-epoch loss haircut is keyed to the funding epoch instead of the request epoch, letting receipts claim at par from an underfunded vault - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The external bug class is a path-guard bypass: the guard validates one representation of the identifier (`..` + forward slashes) while the actual resource lookup uses another (absolute/backslash paths), so the guard never applies. The analog in `IdleCreditVault` is the loss-adjusted withdraw haircut: `collectWithdrawFunds` records the haircut under the epoch in which funds are collected (`lossRecoveryPriceByEpoch[epochNumber]`), while the claim path looks the haircut up under the epoch in which the receipt was *requested* (`lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`). Whenever these two keys differ — which is the normal flow, since receipts requested during the buffer/epoch N are funded at a later `stopEpochWithDuration` after `epochNumber` has advanced — the haircut lookup misses and the unfunded remainder is silently treated as fully funded.

### Finding Description
`collectWithdrawFunds` (called by the CDO during `stopEpochWithDuration`) writes the pro-rata haircut to `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, but only transfers the reduced `_amount` of underlying [1](#0-0) . On the claim side, `_claimLossAdjustedWithdrawRequest` resolves the haircut epoch via `lastWithdrawRequest[_user]` — the value stored in `requestWithdraw` at request time (pre-stop) [2](#0-1) [3](#0-2) . If `epochNumber` at collection time (N+k) is not equal to the request epoch (N) recorded per user, `lossRecoveryPriceByEpoch[N]` is `0`, `_claimLossAdjustedWithdrawRequest` returns 0, and execution falls through to `_claimFundedWithdrawRequest`, whose only gate is `epochNumber <= lastWithdrawRequest[_user]` — already satisfied after the stop [4](#0-3) . The funded path then pays the full un-haircutted `withdrawsRequests[_user]` at par even though the vault only received the reduced amount. The mirror-image guard in `requestWithdraw` (reverting when `lossRecoveryPriceByEpoch[lastWithdrawRequest]` is non-zero) also keys on the request epoch, so it does not catch this either [5](#0-4) .

### Impact Explanation
After a `stopEpochWithDuration` realizing a loss, pending receipts are collectively funded with less than `pendingWithdraws`. Because the haircut is stored under the wrong key, the first users to call `claimWithdrawRequest` withdraw their full nominal basis, draining underlying that belongs to other receipt holders; later claimants' transfers revert for insufficient balance. Net effect: direct overpayment/ theft of other users' funded claims plus permanent freezing of the residual claims, i.e., a broken loss-waterfall and one-receipt-one-payout invariant, with loss magnitude up to the un-applied haircut (the pending-loss share computed in `previewLossAdjustedWithdrawFunds` [6](#0-5) ).

### Likelihood Explanation
Requires only an unprivileged tranche holder who requested a withdraw before the loss epoch and an honest manager calling `stopEpochWithDuration` with a partial funding amount — a normal loss-handling path. No privileged misbehavior is needed; the attacker just claims earlier than other receipt holders.

### Recommendation
Key `lossRecoveryPriceByEpoch` consistently by the request epoch: in `collectWithdrawFunds`, store the haircut under the epoch(s) that actually own the pending receipts (e.g., `epochNumber - 1` or an explicit epoch argument from the CDO matching `lastWithdrawRequest`), or have the claim path resolve the haircut via the collection epoch rather than `lastWithdrawRequest`. Equivalently, since `pendingWithdraws` is a single aggregate bucket, store a single global `lossRecoveryPrice` instead of a per-epoch map so no key translation exists to bypass.

### Proof of Concept
Foundry fork sketch: (1) deposit AA/BB, run epoch 1, `requestWithdraw` during the post-stop buffer so `lastWithdrawRequest[user] = N`; (2) run epoch 2 and call `stopEpochWithDuration` with `_lossAmount > 0`, so `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[M]` with `M > N` and transfers only `pendingToFund`; (3) `vm.prank(user)` `cdoEpoch.claimWithdrawRequest()`; assert `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] == 0` while the transfer paid `amount * 1.0` — the full basis — from a vault holding only the haircutted amount; assert a second receipt holder's identical claim reverts on `safeTransfer` for insufficient balance. Caveat: I could not verify the exact line where `epochNumber` increments (in `IdleCDOEpochVariant.stopEpoch`/`onStopEpoch` ordering); the bug holds whenever the collection-time `epochNumber` differs from any user's request epoch, which the code comments ("buffer + epochDuration is 1 epoch") indicate is the standard flow.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-293)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
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
