### Title
Loss-adjusted withdraw haircut is keyed to the wrong epoch, letting under-funded receipts claim at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Zephyr bug validated a command against `buf->len` (the whole PDU) instead of `len` (the per-command field), so a length check passed on the wrong scope and downstream accounting ran out of bounds. The same per-scope/aggregate confusion exists in `IdleCreditVault`: `collectWithdrawFunds` computes the loss haircut against the aggregate `pendingWithdraws` but stores `lossRecoveryPriceByEpoch[epochNumber]` under the *current* epoch counter, while `_claimLossAdjustedWithdrawRequest` looks the price up by `lastWithdrawRequest[_user]` — the *request* epoch. These two indices diverge, so the haircut recorded against the aggregate is never applied to the per-user receipt.

### Finding Description
In `IdleCreditVault.requestWithdraw` a user receipt is tagged with the epoch it was made in: `lastWithdrawRequest[_user] = currentEpoch` where `currentEpoch = epochNumber` [1](#0-0) . When `stopEpoch`/`stopEpochWithDuration` under-funds pending receipts, `collectWithdrawFunds` zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` [2](#0-1) . But `deposit()` bumps `epochNumber += 1` during the same `stopEpoch` while `isEpochRunning` is still true [3](#0-2) . At claim time, `_claimLossAdjustedWithdrawRequest` only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the request epoch E — while the price was written under the post-increment epoch E+1 [4](#0-3) . The lookup returns 0, so the loss-adjusted path is skipped and `_claimFundedWithdrawRequest` pays the aggregate `withdrawsRequests[_user]` at par [5](#0-4) .

### Impact Explanation
`pendingWithdraws` was zeroed and only `_amount < pendingBasis` was actually collected, yet every receipt pays out unhaircutted. Each claim transfers more underlying than the strategy holds for receipts (`_transferFundedClaim`), draining funds reserved for other requesters or recovery; the last claimants' transfers revert. Result: direct theft / insolvency proportional to `pendingBasis - _amount`, i.e. the full realized loss socialized onto non-claiming users instead of the receipt holders.

### Likelihood Explanation
Requires only a `stopEpochWithDuration`-style loss epoch where the borrower funds less than `pendingWithdraws` — an unprivileged event path (manager/keeper calls honest, loss is a normal protocol state). Any KYC'd lender with a pending receipt then triggers it with a plain `claimWithdrawRequest`. The `lossRecoveryPrice == 0` and `defaultRecoveryInitialized` guards do not block it; the only mitigating factor is the exact ordering of the `epochNumber` bump versus `collectWithdrawFunds` inside `stopEpoch`, which I could not fully trace in `IdleCDOEpochVariant.stopEpoch` within the available iterations — if the collect runs *before* the increment the key matches and the finding collapses, so this ordering must be confirmed in the PoC.

### Recommendation
Store the haircut under the epoch the receipts belong to (the pre-increment request epoch), or make `_claimLossAdjustedWithdrawRequest` iterate the user's per-epoch receipts (`withdrawsRequestsByEpoch`) and apply `lossRecoveryPriceByEpoch` per epoch rather than only via `lastWithdrawRequest`. Apply the same epoch-scoped clearing to APR0 principal/interest.

### Proof of Concept
Foundry fork sketch:

```solidity
// Epoch E running, alice (KYC'd) holds AA tranches
cdo.requestWithdraw(amount, AA);               // lastWithdrawRequest[alice] = E
// stopEpochWithDuration with loss: borrower funds < pendingWithdraws
vm.prank(manager); cdo.stopEpochWithDuration(dur, apr, interest, loss);
// IdleCreditVault.collectWithdrawFunds(funded < basis):
//   pendingWithdraws = 0; lossRecoveryPriceByEpoch[E+1] = price   // bumped epoch
// Alice claims:
cdo.claimWithdrawRequest();
// _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[E] == 0 -> skipped
// _claimFundedWithdrawRequest pays withdrawsRequests[alice] at PAR
assertGt(underlying.balanceOf(alice), fundedShare); // unhaircutted payout
// last claimer's claim reverts / vault reserve drained
```

Confirm by logging `epochNumber` inside `collectWithdrawFunds` versus `lastWithdrawRequest[alice]`; a mismatch of exactly 1 confirms the keyed-epoch bug.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-282)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
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
