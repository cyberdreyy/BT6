### Title
Loss-haircut bypass via stale `lastWithdrawRequest` epoch key lets a withdrawer claim defaulted/loss-adjusted receipts at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` stores the haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `_claimLossAdjustedWithdrawRequest` looks up the loss price under `lastWithdrawRequest[_user]` — the user's *latest* request epoch, not the epoch that actually took the loss. By placing a second withdraw request after the loss-adjusted epoch, a user moves the lookup key to an epoch with no recovery price, so the loss-adjusted path is skipped entirely and the old haircutted receipt is paid out 1:1 by `_claimFundedWithdrawRequest`. This is the same "use-after-free" class as CVE-2025-21715: an epoch-scoped record (`lossRecoveryPriceByEpoch[lossEpoch]`) is effectively orphaned/freed for that user once `lastWithdrawRequest` is overwritten, yet the stale aggregate receipt (`withdrawsRequests`/`withdrawsRequestsByEpoch` for the loss epoch) is still dereferenced and paid at par.

### Finding Description
In `requestWithdraw`, `lastWithdrawRequest[_user]` is set to `epochNumber` at request time and `withdrawsRequestsByEpoch[_user][currentEpoch]` accumulates the basis [1](#0-0) . When the borrower under-funds pending withdrawals at `stopEpochWithDuration`, `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws`, but does **not** reduce `withdrawsRequests[_user]` or `withdrawsRequestsByEpoch` — the haircut only materializes at claim time [2](#0-1) .

At claim time, `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest` first, which derives the loss epoch as `lastWithdrawRequest[_user]` [3](#0-2) . If the user requested again in a later epoch, `lastWithdrawRequest` no longer equals the loss epoch, `lossRecoveryPriceByEpoch[lossEpoch]` is 0, and the function returns without clearing the loss-epoch receipt. `_claimFundedWithdrawRequest` then only checks that `epochNumber > lastWithdrawRequest[_user]` and pays the full aggregate `withdrawsRequests[_user]` — including the never-cleared loss-epoch basis — at par [4](#0-3) . `requestWithdraw` imposes no restriction on re-requesting while a receipt is pending (the code comments explicitly document that re-requesting only delays the claim) [5](#0-4) .

The same key-mismatch applies on the default path: `_claimDefaultedWithdrawRequest` clears `defaultRecoveryEpoch`'s basis, and `_clearWithdrawClaimForEpoch` only resets `lastWithdrawRequest` when it happens to equal the cleared epoch [6](#0-5) .

### Impact Explanation
The loss-waterfall invariant is broken. For a loss-adjusted epoch the borrower only funded `claimBasis * lossRecoveryPrice`, yet the attacker withdraws the full `claimBasis`. The deficit `claimBasis * (1 - lossRecoveryPrice)` is paid from strategy underlyings backing other users' funded receipts and active LPs — direct insolvency / theft of unclaimed yield. With a 50% recovery price and a large attacker receipt, the attacker doubles their entitlement relative to honest claimants, and late claimants' `_transferFundedClaim` calls revert or drain the reserve.

### Likelihood Explanation
All steps are unprivileged: deposit → `requestWithdraw` (epoch N) → wait for an honest manager `stopEpochWithDuration(_lossAmount > 0)` (a normal protocol operation, e.g., partial borrower shortfall) → `requestWithdraw` again in epoch N+1 → wait one epoch → `claimWithdrawRequest`. No privileged misbehavior, no oracle manipulation, no race. The only precondition is a realized loss epoch, which is precisely the scenario `lossRecoveryPriceByEpoch` exists for, so the accounting path is guaranteed to be reachable whenever it matters.

### Recommendation
Attribute loss epochs per receipt, not per user. Options: (a) store `lossRecoveryPriceByEpoch` keyed lookup against every epoch present in `withdrawsRequestsByEpoch[_user]` (iterate or track a per-user loss-epoch list); (b) record the loss price against each user's request epoch at request time; or (c) simplest, in `_claimLossAdjustedWithdrawRequest` also check `lossRecoveryPriceByEpoch` for epochs with non-zero `withdrawsRequestsByEpoch[_user][*]` rather than trusting `lastWithdrawRequest`. At minimum, `requestWithdraw` should revert if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the prior receipt is unclaimed, preventing the key from being overwritten while a haircutted receipt is outstanding.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCDOEpochQueue.t.sol`'s `testProcessWithdrawalClaimsWithZeroRoundedQueuePayout` loss setup [7](#0-6) ):

```solidity
// 1. Attacker deposits in epoch N-1 buffer, epoch N starts.
// 2. Attacker calls cdo.requestWithdraw(trancheAmt, tranche) -> lastWithdrawRequest[attacker] = N,
//    withdrawsRequestsByEpoch[attacker][N] = amt.
// 3. stopEpochWithDuration(apr, 0, duration, lossAmount) with a partial loss:
//    previewLossAdjustedWithdrawFunds -> pendingToFund < pendingBasis;
//    collectWithdrawFunds(pendingToFund) -> lossRecoveryPriceByEpoch[N] = p < RECOVERY_FULL.
// 4. Epoch N+1: attacker calls requestWithdraw(x, tranche) again -> lastWithdrawRequest[attacker] = N+1.
// 5. Epoch N+2 begins (epochNumber > lastWithdrawRequest). Attacker calls cdo.claimWithdrawRequest():
//    - _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N+1] == 0 -> skip.
//    - _claimFundedWithdrawRequest: pays withdrawsRequests[attacker] = amt + x at PAR.
// assert attacker received amt + x instead of amt*p/RECOVERY_FULL + x;
// assert strategy underlying balance < reserve needed for remaining claimants.
```

The invariant violation is demonstrable purely through the epoch-key mismatch in `_claimLossAdjustedWithdrawRequest` (line 790) versus the per-epoch storage written by `requestWithdraw` (line 293) and `collectWithdrawFunds` (line 421).

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L323-325)
```text
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-426)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-837)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }

  /// @notice Claim a stopEpochWithDuration loss-adjusted withdraw receipt.
  /// @param _user address of the user
  /// @return amount amount paid from funded strategy underlyings
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

  /// @notice Clear a normal/APR0 withdraw receipt for one request epoch.
  /// @dev This does not move funds or burn receipt tokens. Default claims also decrease
  /// `apr0TotalPrincipal`; loss-adjusted claims do not because stopEpoch already closed that bucket.
  /// @param _user address of the user
  /// @param _claimEpoch epoch whose receipt should be cleared
  /// @param _isClearingApr0 true when clearing an open APR0 default claim
  /// @return claimBasis claim amount before applying the recovery ratio
  /// @return burnAmount strategy-token receipt amount to burn
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

**File:** test/foundry/IdleCDOEpochQueue.t.sol (L817-831)
```text
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest();
    uint256 totalBasis = activeBasis + pendingBasis;
    uint256 totalRecovery = totalBasis / 100_000;
    uint256 lossAmount = totalBasis - totalRecovery;
    (uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(lossAmount);
    uint256 fundsToRepay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(address(underlying), strategy.borrower(), fundsToRepay, true);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), fundsToRepay);

    uint256 duration = cdoEpoch.epochDuration();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(10e18, 0, duration, lossAmount);

```
