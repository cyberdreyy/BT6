### Title
Loss-adjusted withdraw claims apply the haircut only to the latest request epoch; older unclaimed receipts are paid at par and drain funds owed to other claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` computes `lossRecoveryPrice` over the aggregate `pendingWithdraws` basis, which includes every unclaimed receipt across epochs, but `_claimLossAdjustedWithdrawRequest` only haircut-applies the receipt stored under `lastWithdrawRequest[_user]`. Any older per-epoch receipt (`withdrawsRequestsByEpoch[user][olderEpoch]`) then falls through to `_claimFundedWithdrawRequest` and is paid 1:1, exactly like the reported "latest snapshot only" aggregation bug — except inverted: the stale portion escapes the loss entirely.

### Finding Description
When a stop epoch underfunds pending withdrawals, `collectWithdrawFunds` records `lossRecoveryPriceByEpoch[epochNumber] = funded * 1e18 / pendingBasis` where `pendingBasis` is the *global* `pendingWithdraws` aggregate [1](#0-0) . The guard in `requestWithdraw` only blocks a new request when the user's *last* request epoch already has a nonzero loss price [2](#0-1) . Nothing prevents a user from holding receipts in multiple epochs when earlier stops were fully funded: `lastWithdrawRequest[_user]` is overwritten each request [3](#0-2) .

On claim, `_claimLossAdjustedWithdrawRequest` resolves only `lastWithdrawRequest[_user]`'s epoch basis via `_clearWithdrawClaimForEpoch` and pays it at `lossRecoveryPrice` [4](#0-3) ; the remainder in `withdrawsRequests[_user]` is then paid at par by `_claimFundedWithdrawRequest` [5](#0-4) . Yet the funding that arrived from the borrower only covered `lossRecoveryPrice` of the *entire* aggregate — the older receipt was included in the basis used to compute the haircut but is redeemed un-haircutted.

### Impact Explanation
Direct insolvency/theft among receipt holders: a user with unclaimed receipts spanning a fully-funded epoch and a subsequent loss epoch withdraws more underlyings than the strategy received for them. The shortfall is socialized onto later claimants (last claimer cannot be paid, permanent loss) or onto active LPs, breaking the "one receipt, haircut-proportional payout" invariant of `lossRecoveryPriceByEpoch`.

### Likelihood Explanation
Requires only an unprivileged tranche holder who (1) requests a withdraw in epoch N, (2) does not claim after epoch N is fully funded, (3) requests again in epoch N+1, (4) epoch N+1 ends with `stopEpochWithDuration` loss. No privileged misbehavior needed; the honest borrower/manager call sequence suffices. Multi-epoch unclaimed receipts are a normal usage pattern (claiming is optional and deferred).

### Recommendation
Apply the loss recovery price to *all* of the user's unclaimed basis that was part of the haircutted `pendingWithdraws` aggregate — i.e., in `_claimLossAdjustedWithdrawRequest`, compute the claim over `withdrawsRequests[_user]` (plus open APR0 basis) rather than only `withdrawsRequestsByEpoch[_user][lossEpoch]`, or iterate and clear every per-epoch receipt that predates the loss epoch at the same `lossRecoveryPrice`.

### Proof of Concept
Foundry fork PoC sketch (running epoch mode, fixed APR, non-APR0):

```solidity
// setup: vault with idleCDO, epoch running (epochNumber = N)
// user requests w1 = 100 via IdleCDO.requestWithdraw -> withdrawsRequestsByEpoch[u][N] = 100
// stopEpoch N: borrower fully funds -> collectWithdrawFunds(100): pendingBasis == _amount,
//   else-branch taken, pendingWithdraws = 0, NO lossRecoveryPriceByEpoch[N] set
// user does NOT claim. epoch N+1 starts and runs.
// user requests w2 = 100 -> lastWithdrawRequest[u] = N+1,
//   withdrawsRequestsByEpoch[u][N+1] = 100, pendingWithdraws = 100... 
//   NOTE: also another user requests w3 so aggregate pending > w2
// stopEpoch N+1 with loss: collectWithdrawFunds(50) with pendingBasis = 200
//   -> lossRecoveryPriceByEpoch[N+1] = 0.25e18, pendingWithdraws = 0, strategy holds 50
// user.claimWithdrawRequest():
//   _claimLossAdjustedWithdrawRequest -> clears only epoch N+1 basis 100 -> pays 25
//   _claimFundedWithdrawRequest -> withdrawsRequests[u] still 100 (epoch N) -> pays 100 at par
// user receives 125 total; pool only received 50 for the aggregate 200 basis.
// remaining claimants (w3 holder) are short by 75 -> unpayable.
``` [6](#0-5)

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-313)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
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
