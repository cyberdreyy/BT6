### Title
Loss-adjusted withdraw receipt escapes haircut when the user re-requests in a later epoch — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The ONOS bug class — a recorded "intent" (request) that is inconsistent with the "flow rules" actually enforced — maps directly onto the gap between a withdraw receipt's recorded claim basis and the loss haircut it should have suffered. When `collectWithdrawFunds` applies a partial `lossRecoveryPrice` to an epoch's pending receipts, the haircut is only applied at claim time if that epoch is still the user's `lastWithdrawRequest`. A user who makes a second `requestWithdraw` in a later epoch causes the loss-adjusted receipt to be paid at par through the funded-claim path, over-claiming underlying that was never funded.

### Finding Description
In `IdleCDOEpochVariant.requestWithdraw`, each request calls `IdleCreditVault.requestWithdraw`, which records `withdrawsRequestsByEpoch[user][epochNumber] += _amount` and overwrites `lastWithdrawRequest[user] = epochNumber` [1](#0-0) .

When a stop realizes a loss, `collectWithdrawFunds` is called with `_amount < pendingBasis`: it stores `lossRecoveryPriceByEpoch[epochNumber]`, zeroes `pendingWithdraws`, and pulls only the haircut amount of underlying into the strategy [2](#0-1) . The per-epoch claim basis in `withdrawsRequestsByEpoch` is intentionally kept at full value; the haircut is applied lazily at claim time.

But `_claimLossAdjustedWithdrawRequest` only looks up the haircut for `lastWithdrawRequest[_user]` — the user's most recent request epoch [3](#0-2) . If the user re-requested in a later epoch, `lastWithdrawRequest` points to a fully-funded epoch whose `lossRecoveryPriceByEpoch` entry is 0, so the loss-adjusted branch returns 0. Execution then falls to `_claimFundedWithdrawRequest`, which pays the entire aggregate `withdrawsRequests[_user]` — still including the un-haircutted basis of the loss epoch — at par [4](#0-3) . The receipt tokens minted 1:1 at request time cover the burn, so no guard fires. `_transferFundedClaim` only protects `defaultRecoveryReserve`, not other users' funded receipts [5](#0-4) .

### Impact Explanation
The attacker (a KYC-passed lender) extracts `claimBasis_lossEpoch × (1 − lossRecoveryPrice / RECOVERY_FULL)` underlying that was never transferred into the strategy. The strategy balance is short by exactly the haircut amount, so the over-payment is drained from underlyings funded for other users' receipts: later claimants either receive less or have `claimWithdrawRequest` permanently revert on insufficient balance — direct theft plus insolvency/permanent freezing of unclaimed yield.

### Likelihood Explanation
Requires a `stopEpochWithDuration` partial loss (an honest manager/borrower flow the attacker sequences around, not a malicious role) and one extra `requestWithdraw` by the attacker in any subsequent epoch's buffer — a normal, permitted action. The receipt ordering in `claimWithdrawRequest` (loss-adjusted first, then funded at line 312–313) is precisely the state mismatch the ONOS bug illustrates: the recorded request and the funded enforcement disagree.

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (and claim ordering generally), iterate or track all epochs with a nonzero `lossRecoveryPriceByEpoch` for which `withdrawsRequestsByEpoch[user][epoch] != 0`, not just `lastWithdrawRequest[user]`. Alternatively, store a per-epoch loss flag consumed in `_clearWithdrawClaimForEpoch` so any prior loss-epoch receipt is haircut regardless of subsequent requests.

### Proof of Concept
```solidity
// Fork PoC sketch (Foundry)
// 1. Buffer: attacker KYC'd, deposits AA, then requestWithdraw(attackerBal, AA)
//    -> withdrawsRequestsByEpoch[attacker][N] = basis, lastWithdrawRequest = N
// 2. startEpoch(); borrower returns only part -> manager calls
//    stopEpochWithDuration with _lossAmount so collectWithdrawFunds(haircut):
//    lossRecoveryPriceByEpoch[N] = 0.5e18, pendingWithdraws = 0, strategy gets basis/2
// 3. New buffer/epoch N+1: attacker requestWithdraw(smallAmount, AA)
//    -> lastWithdrawRequest = N+1, withdrawsRequests[attacker] += small
// 4. Epoch N+1 fully funded at stopEpoch (pendingWithdraws collected at par)
// 5. attacker claimWithdrawRequest():
//    _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N+1] == 0 -> 0
//    _claimFundedWithdrawRequest: pays basis(N) + amount(N+1) at par
// 6. assert: attacker received basis/2 more than funded; strategy balance
//    < sum of remaining users' receipts -> last claimant reverts or is underpaid
```
Note: the PoC assumes `epochNumber` is incremented across the `stopEpoch`/`deposit`-during-running boundary so epochs N and N+1 are distinct keys; the mechanism does not depend on the exact increment site, only on a second request overwriting `lastWithdrawRequest`.

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
