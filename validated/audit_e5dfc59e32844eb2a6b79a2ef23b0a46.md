### Title
Loss-adjusted withdraw receipts escape their haircut and are paid at par when the user requests a new withdraw before claiming - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration` realizes a loss, pending withdraw receipts are only partially funded and each user's claim is supposed to be paid pro rata via `lossRecoveryPriceByEpoch`. The loss-adjusted claim path is keyed solely on `lastWithdrawRequest[_user]`, which is overwritten on every new `requestWithdraw`. A lender who holds a haircut receipt from the loss epoch can simply make a second withdraw request in a later epoch, wait one more epoch, and claim the *full* unhaircutted `withdrawsRequests[_user]` balance at par through `_claimFundedWithdrawRequest`, bypassing the recovery-price verification entirely — the analog of crafting input to bypass GRUB's verification (CVE-2020-10713).

### Finding Description
- On a lossy stop, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and sets `pendingWithdraws = 0`, while each user's full claim basis stays in `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][epoch]` [1](#0-0) .
- `claimWithdrawRequest` applies the haircut only through `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` [2](#0-1) .
- `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and only *adds* to `withdrawsRequests[_user]` [3](#0-2) .
- So once the user requests again in a clean epoch N+1, `lossRecoveryPriceByEpoch[N+1] == 0`, the loss path returns 0, and `_claimFundedWithdrawRequest` pays `withdrawsRequests[_user]` (haircutted basis + new basis) at par via `_transferFundedClaim` [4](#0-3) .
- The strategy only received `claimBasis * lossRecoveryPrice / RECOVERY_FULL` for the loss-epoch receipts, so the excess is paid out of underlyings collected for other pending receipts or active positions.
- No guard stops this: `requestWithdraw` does not require claiming prior receipts (the funded-claim comment even acknowledges stacked requests), `_checkNotAllowed` epoch gating only enforces a one-epoch wait, and `_transferFundedClaim`'s reserve guard only protects `defaultRecoveryReserve`, not funded-claim solvency [5](#0-4) .

### Impact Explanation
Direct theft / insolvency: the attacker recovers `claimBasis` instead of `claimBasis * lossRecoveryPrice`, stealing the haircut difference (up to ~100% of the receipt for near-total losses) from other withdraw claimants, who then find the strategy underfunded (permanent freezing of their claims). Loss scales with receipt size and loss severity; a whale receipt in a 50%-loss epoch steals ~50% of its basis.

### Likelihood Explanation
Requires only a KYC-passed lender: deposit, `requestWithdraw` before a lossy `stopEpochWithDuration`, then `requestWithdraw` again in the next buffer period and claim after one more epoch. All steps are normal user flows; the trigger (a manager-called lossy stop) is an expected protocol event, not attacker manipulation of a privileged role.

### Recommendation
In `_claimFundedWithdrawRequest`, iterate or check all epochs with `lossRecoveryPriceByEpoch` for the user's `withdrawsRequestsByEpoch` entries instead of keying the loss claim only on `lastWithdrawRequest`; alternatively, in `requestWithdraw`, force-settle any existing loss-adjusted receipt (or revert if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the receipt is unclaimed) before overwriting `lastWithdrawRequest`.

### Proof of Concept
Foundry fork outline:
1. `depositAA` as attacker; manager `startEpoch`; attacker `requestWithdraw(all)`.
2. Manager `stopEpochWithDuration(newApr, interest, duration, lossAmount)` with `lossAmount` such that `previewLossAdjustedWithdrawFunds` funds ~50% of `pendingWithdraws` → `lossRecoveryPriceByEpoch[N] ≈ 0.5e18`.
3. In buffer of epoch N+1 attacker deposits a small amount and calls `requestWithdraw` again → `lastWithdrawRequest = N+1`.
4. Manager `startEpoch`, then `stopEpoch` fully funded.
5. Attacker `claimWithdrawRequest`: assert payout equals `basis_N + basis_{N+1}` at par instead of `basis_N * 0.5 + basis_{N+1}`; assert a second claimant's later claim reverts/underpays due to drained strategy balance.

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
