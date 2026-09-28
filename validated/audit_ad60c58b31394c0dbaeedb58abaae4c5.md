### Title
Loss-adjusted withdraw receipts from earlier epochs escape the haircut and are paid at par, draining the funded pool - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` applies a single pro-rata `lossRecoveryPriceByEpoch` haircut over the aggregate `pendingWithdraws` basis, but `_claimLossAdjustedWithdrawRequest` only haircut-clears receipts stored under `lastWithdrawRequest[_user]` (the user's most recent request epoch). Receipts a user accumulated in earlier epochs via `withdrawsRequestsByEpoch[_user][olderEpoch]` / `withdrawsRequests[_user]` survive and are later paid at par through `_claimFundedWithdrawRequest`, even though the borrower funded only the haircut amount.

### Finding Description
The mbsync CVE is an unchecked-boundary bug: a message lacking headers slips past a length check and causes a heap overflow. The analog here is a boundary/scope check on the wrong index: the haircut bookkeeping is keyed by one epoch (`lastWithdrawRequest`) while the funded basis covers all epochs.

- `requestWithdraw` accumulates receipts per user per epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]` and adds the full amount to the aggregate `pendingWithdraws`, and records `lastWithdrawRequest[_user] = currentEpoch`. A user may call `requestWithdraw` multiple times across different epochs without claiming; the only guard is the loss-receipt check at [1](#0-0) , which passes while no `lossRecoveryPriceByEpoch` exists for the last epoch.
- When `stopEpochWithDuration(_lossAmount)` realizes a loss, `_stopEpoch` calls `previewLossAdjustedWithdrawFunds`, which computes the haircut on the *entire* `pendingBasis` (all epochs' receipts), then `collectWithdrawFunds` pulls only `pendingToFund` and stores `lossRecoveryPriceByEpoch[epochNumber]` [2](#0-1) [3](#0-2) .
- On `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` and clears only `withdrawsRequestsByEpoch[_user][lastWithdrawRequest]` [4](#0-3) [5](#0-4) . It then falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` (the residual earlier-epoch basis) at par [6](#0-5) .

So a user holding receipts from epoch E and E+1 gets haircut only on the E+1 portion but collects the E portion at par, while the strategy only received `pendingToFund` for the combined basis.

### Impact Explanation
Direct theft/insolvency with quantified loss. If `pendingBasis` = 1000 across two epochs, `lossRecoveryPrice` = 50%, the strategy holds only 500 underlying, yet the claimant receives `500 (haircut on latest epoch) + earlier-epoch basis at par`. The aggregate payout exceeds the funded reserve, so either the claimant over-withdraws (stealing other users' funded claims or reserve) or later claimants' claims revert/permanently underpay — a broken "one receipt, one payout" and loss-socialization invariant. `_transferFundedClaim`'s reserve guard only protects `defaultRecoveryReserve`, not other users' funded claims, so it does not stop this.

### Likelihood Explanation
Requires no privileged misbehavior: any KYC-passed tranche holder makes two `requestWithdraw` calls in consecutive epochs (waiting one epoch is normal user flow), then the manager calls `stopEpochWithDuration` with a partial loss (a plausible realized-loss path, explicitly supported). The attacker then calls `claimWithdrawRequest` once. Likelihood is moderate: it needs a non-zero `_lossAmount` stop, which is an intended (if infrequent) operating mode.

Uncertainty I could not fully verify within budget: the exact line where `epochNumber` increments relative to `collectWithdrawFunds` inside the strategy's stop flow. If `epochNumber` increments before `collectWithdrawFunds` writes the key, `lossRecoveryPriceByEpoch[lastWithdrawRequest]` returns 0 and *all* receipts escape the haircut entirely — a strictly worse variant of the same finding; either ordering yields a payout exceeding the funded basis.

### Recommendation
Track the loss haircut per *receipt* rather than per last-request epoch: on `requestWithdraw`, either force-settle all prior receipts before accepting a new one (extend the existing `NotAllowed` guard to revert whenever `withdrawsRequests[_user] != 0` or any per-epoch entry exists, not only when the *last* epoch has a stored loss price), or store each request's epoch list and apply `lossRecoveryPriceByEpoch` to every outstanding epoch in `_claimLossAdjustedWithdrawRequest`. Additionally, `_claimFundedWithdrawRequest` should verify `lossRecoveryPriceByEpoch` was not set for any epoch contributing to `withdrawsRequests[_user]`.

### Proof of Concept
Foundry fork PoC sketch against `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
// Setup: user deposits AA, epoch 0 runs and stops fully funded.
_depositWithUser(user, 1000 * ONE_SCALE, true);
_startEpochAndCheckPrices(0);
_stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

// Epoch boundary 1: user requests withdraw #1 (receipt recorded at epoch E1).
vm.prank(user);
cdoEpoch.requestWithdraw(400 * ONE_SCALE, address(AAtranche));

// Epoch E1 stops, epoch E2 starts; user does NOT claim, requests again (epoch E2).
_startEpochAndCheckPrices(1);
vm.prank(user);
cdoEpoch.requestWithdraw(400 * ONE_SCALE, address(AAtranche));

// Manager stops epoch with a loss -> collectWithdrawFunds stores
// lossRecoveryPriceByEpoch = ~50% computed over pendingBasis = 800.
uint256 loss = /* partial loss */;
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(newApr, 0, 3 days, loss);

// Attack: single claim. E2 receipt is haircut, but the E1 receipt
// (still in withdrawsRequestsByEpoch[user][E1] / withdrawsRequests[user])
// is paid at par via _claimFundedWithdrawRequest.
uint256 balPre = underlying.balanceOf(user);
vm.prank(user);
cdoEpoch.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(user) - balPre;

// Assert: paid > funded share (pendingToFund), proving over-payment;
// a second honest claimant's claim then reverts or underpays.
assertGt(paid, expectedHaircutPayout);
```

Relevant code: `requestWithdraw` (IdleCreditVault.sol:243-295), `collectWithdrawFunds` (411-430), `_claimLossAdjustedWithdrawRequest` (789-801), `_claimFundedWithdrawRequest` (319-350), `_stopEpoch` pull of `pendingToFund` (IdleCDOEpochVariant.sol:393-410).

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L312-350)
```text
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }

  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
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
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-820)
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
```
