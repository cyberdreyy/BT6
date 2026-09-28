### Title
Uninitialized epoch-0 slot read as a valid loss-adjusted receipt epoch due to `epochNumber` increment before `lossRecoveryPriceByEpoch` is keyed - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is "an uninitialized/default slot is fetched and treated as a valid entry." The analog in `IdleCreditVault` is the epoch index itself: `lastWithdrawRequest`, `withdrawsRequestsByEpoch`, `apr0Users.principalEpoch`, `instantWithdrawClaimsByEpoch`, and `lossRecoveryPriceByEpoch` all use epoch 0 both as a real request epoch and as the "empty" sentinel, and `collectWithdrawFunds` writes the haircut price under `epochNumber` *after* `deposit()` has already incremented it during `stopEpoch`, while the receipts were recorded under the pre-increment epoch.

### Finding Description
- `requestWithdraw` stores `lastWithdrawRequest[_user] = epochNumber` and `withdrawsRequestsByEpoch[_user][epochNumber] += _amount` under the current (pre-stop) epoch [1](#0-0) .
- During `stopEpoch`, the CDO's `deposit()` call into the strategy runs while `isEpochRunning()` is still true, so `epochNumber += 1` executes first [2](#0-1) .
- When the borrower under-funds pending receipts, `collectWithdrawFunds` then writes `lossRecoveryPriceByEpoch[epochNumber]` — i.e. under the *incremented* epoch `N+1`, while every haircut receipt is tagged with `lastWithdrawRequest == N` [3](#0-2) .
- `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (epoch `N`), reads the uninitialized `0` entry — exactly the "empty slot returned as valid" pattern — and returns early, so the claim falls through to `_claimFundedWithdrawRequest` [4](#0-3) .
- `_claimFundedWithdrawRequest` then pays the full `withdrawsRequests[_user]` at par because the gate `epochNumber <= lastWithdrawRequest` passes (`N+1 > N`), even though only the haircut amount was ever collected and `pendingWithdraws` was zeroed [5](#0-4) .
- The epoch-0 sentinel conflation also exists in `requestWithdraw`: `lossEpoch = lastWithdrawRequest[_user]` is `0` both for "never requested" and for a genuine epoch-0 request, so the "claim loss-adjusted receipt before a new request" guard can key off an empty slot [6](#0-5) .

### Impact Explanation
Receipt holders withdraw more underlying than the borrower funded after a lossy `stopEpochWithDuration`. Early claimers are paid at par; the funding shortfall is socialized onto later claimants whose `safeTransfer` reverts (insolvency / permanent freezing of the unfunded remainder). The broken invariant is "one receipt, one haircut-adjusted payout" — identical in spirit to the uninitialized `LockedStake` being finalized as valid.

### Likelihood Explanation
Requires a stop epoch where `_amount < pendingWithdraws` in `collectWithdrawFunds` (a `stopEpochWithDuration` partial-loss flow) on an initialized post-upgrade vault. Honest keeper/manager calls trigger it; any pending-receipt holder can then claim. Caveat: exploitability depends on the CDO calling `deposit()` (which bumps `epochNumber`) *before* `collectWithdrawFunds` inside the same `stopEpoch`; I was unable to fully read `IdleCDOEpochVariant.stopEpoch` ordering in the available index, so this must be confirmed in the PoC.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the request epoch of the receipts being haircut (i.e. `epochNumber` before the increment, or an explicit `_epoch` parameter), and reserve a dedicated sentinel (e.g. `type(uint256).max`) for "no request" in `lastWithdrawRequest` so epoch 0 is never conflated with an uninitialized slot — the same fix class as "check the locked stake is initialized."

### Proof of Concept
```solidity
// Foundry fork PoC outline (test/foundry/IdleCreditVault.t.sol harness):
// 1. depositAA as LP, _startEpochAndCheckPrices(0), _stopEpochAndCheckPrices -> epochNumber = 1
// 2. LP calls cdoEpoch.requestWithdraw(...) during buffer
//    -> strategy.lastWithdrawRequest(LP) == 1, withdrawsRequestsByEpoch[LP][1] = amt
// 3. manager starts epoch 1, borrower under-funds: stopEpochWithDuration(_lossAmount)
//    path -> strategy.deposit() bumps epochNumber to 2,
//    -> collectWithdrawFunds(partial) writes lossRecoveryPriceByEpoch[2] != 0
// 4. LP calls cdoEpoch.claimWithdrawRequest()
//    -> _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[1] == 0 (uninitialized)
//    -> _claimFundedWithdrawRequest pays full `amt` at par
// 5. assert LP received amt > lossRecoveryPrice-adjusted value;
//    second receipt holder's claim reverts on safeTransfer (insolvency).
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L260-294)
```text
    uint256 currentEpoch = epochNumber;
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
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-421)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
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
