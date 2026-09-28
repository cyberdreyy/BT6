### Title
Loss-adjusted pending receipts from earlier epochs are paid at par — multi-epoch withdraw-requesters drain funded strategy balance - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` applies a single pro-rata haircut (`lossRecoveryPriceByEpoch`) to the **aggregate** `pendingWithdraws` basis, which mixes receipts created in multiple request epochs. However, `_claimLossAdjustedWithdrawRequest` only clears the receipt stored under `lastWithdrawRequest[_user]` (the user's most recent request epoch). Earlier-epoch receipts remain in `withdrawsRequests[_user]` and are paid at par via `_claimFundedWithdrawRequest`, even though the borrower funded them only at the haircut ratio. This mirrors CVE-2018-5308: an aggregate "copy" of the pending basis is performed without validating the per-epoch composition, so a claimant can extract more than was funded.

### Finding Description
`requestWithdraw` increments the global `pendingWithdraws` and tags only `lastWithdrawRequest[_user]` with the current `epochNumber` [1](#0-0) . When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` over the whole aggregate and zeroes `pendingWithdraws` [2](#0-1) . On claim, `_claimLossAdjustedWithdrawRequest` uses `lossEpoch = lastWithdrawRequest[_user]` and clears only that epoch's per-epoch basis via `_clearWithdrawClaimForEpoch` [3](#0-2) . The remaining `withdrawsRequests[_user]` balance (earlier epochs' receipts) then pays out 1:1 in `_claimFundedWithdrawRequest` [4](#0-3) .

Attack path (epoch phase: running → stopped with loss):
1. Attacker (KYC'd lender) requests withdraw `X` in epoch N, then requests `Y` in epoch N+1. `lastWithdrawRequest = N+1`; `pendingWithdraws = X + Y` — both haircutted pro rata.
2. Manager stops epoch with `_lossAmount`; `collectWithdrawFunds` funds `(X+Y) * r` where `r < 1`, storing `lossRecoveryPriceByEpoch[epochNumber] = r`.
3. Attacker calls `cdoEpoch.claimWithdrawRequest()`: epoch-N+1 receipt pays `Y*r`; then `withdrawsRequests[_user]` still holds `X`, paid at par `X`. Total `Y*r + X > (X+Y)*r`.
4. The excess `X*(1-r)` is taken from strategy underlyings reserved for other claimants, breaking "one receipt, one (funded) payout" and solvency for remaining receipt holders.

The guard in `requestWithdraw` [5](#0-4)  only blocks *new* requests after a haircut on the last epoch; it does not prevent mixing of unhaircutted-era and haircutted receipts already accumulated across epochs, nor does it tag each epoch's receipts with the haircut they were funded at.

### Impact Explanation
Direct overpayment: any user with pending withdraw receipts spanning ≥2 epochs before a loss-adjusted stop claims their earlier-epoch basis at full price while it was funded only at `r`. For `r = 0.5` and `X = Y`, the attacker extracts ~25% more than funded per aggregate, draining the vault's funded balance and causing insolvency for later claimants of the same loss epoch.

### Likelihood Explanation
Requires only: an unprivileged KYC'd lender making withdraw requests in two distinct epochs, followed by a `stopEpochWithDuration(_lossAmount)` (manager-initiated, honest) producing `lossRecoveryPriceByEpoch` < 1. No privileged collusion needed; the overclaim is a pure arithmetic path in `claimWithdrawRequest`. Note: whether `lossEpoch` as recorded equals the epoch under which the haircut is stored depends on `epochNumber` increment timing in `deposit()` — this should be confirmed in a PoC, since if the haircut epoch always equals `lastWithdrawRequest`, the older receipts may instead be trapped (permanent freezing of user funds, still a valid impact).

### Recommendation
Either (a) record the loss haircut per request-epoch by iterating/mark all epochs contributing to `pendingBasis` — e.g., store the loss epoch coverage and apply `lossRecoveryPriceByEpoch` to every unclaimed receipt epoch that was pending at that stop, not only `lastWithdrawRequest`; or (b) maintain a per-epoch `pendingWithdrawsByEpoch` and let `collectWithdrawFunds` write the recovery price to each contributing epoch. Additionally, `_claimFundedWithdrawRequest` should revert/skip receipts whose epoch carries a non-zero `lossRecoveryPriceByEpoch`.

### Proof of Concept
Foundry fork sketch (repo: `Thankgod67Ikhide/idle-tranches--019`, test base `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testMultiEpochReceiptOverclaim() external {
    // attacker = KYC'd user
    _depositWithUser(attacker, 20_000 * ONE_SCALE, true);

    // epoch N: request withdraw X
    vm.prank(attacker);
    uint256 x = cdoEpoch.requestWithdraw(half, address(AAtranche));
    _startEpochAndCheckPrices(0); // epoch N running
    // stop epoch N normally -> epochNumber bumps
    // epoch N+1: request withdraw Y
    vm.prank(attacker);
    uint256 y = cdoEpoch.requestWithdraw(rest, address(AAtranche));
    // manager stops epoch N+1 WITH a loss via stopEpochWithDuration
    // -> collectWithdrawFunds funds (x+y)*r, r < 1
    // attacker claims: pays y*r + x  (x at par)
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - balPre, (x + y) * r / 1e18);
}
```

Key assertion to verify first in the PoC: whether `lastWithdrawRequest[attacker]` equals the epoch index used as key in `lossRecoveryPriceByEpoch` (i.e., `epochNumber` value at `collectWithdrawFunds` time vs. request time, given `epochNumber += 1` inside `deposit()` during a running epoch). If they differ, the same code path yields permanent freezing of the older receipt instead — either outcome breaks the one-receipt-one-payout invariant.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L263-271)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L277-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-426)
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
