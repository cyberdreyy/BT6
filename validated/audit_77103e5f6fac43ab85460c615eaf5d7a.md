### Title
Loss-epoch withdraw receipt escapes haircut when a newer request overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The use-after-free analog is a stale/freed pointer: `IdleCreditVault` tracks a user's loss-adjusted claim with a single `lastWithdrawRequest[_user]` epoch pointer. When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores the haircut under `lossRecoveryPriceByEpoch[epochNumber]` for the epoch that was just stopped. If the same user makes a new `requestWithdraw` in a later epoch before claiming, `lastWithdrawRequest` is overwritten to the new epoch, `_claimLossAdjustedWithdrawRequest` looks up the wrong epoch, finds `lossRecoveryPriceByEpoch == 0`, and the haircutted receipt falls through to `_claimFundedWithdrawRequest`, which pays it at par.

### Finding Description
In `requestWithdraw` the strategy unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and adds the amount to both `withdrawsRequests[_user]` (aggregate) and `withdrawsRequestsByEpoch[_user][currentEpoch]` (per-epoch) [1](#0-0) . The code comments explicitly acknowledge the multi-request case: "if a user does not claim a withdraw request and instead requests another withdraw, he will have to wait for another epoch to claim both requests" [2](#0-1) .

When a loss is realized, `collectWithdrawFunds` records `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and zeroes `pendingWithdraws` [3](#0-2) . On claim, `_claimLossAdjustedWithdrawRequest` derives the loss epoch solely from `lastWithdrawRequest[_user]` [4](#0-3) . If that pointer has been moved to a newer epoch, `lossRecoveryPrice` is 0, the function returns early without clearing the old per-epoch entry, and `_claimFundedWithdrawRequest` then pays the entire `withdrawsRequests[_user]` aggregate — including the haircutted receipt — at full value via `_transferFundedClaim` [5](#0-4) .

Broken invariant: the loss waterfall. The loss-adjusted funding collected via `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` assumes all pending receipts of the loss epoch share `pendingLoss` pro rata [6](#0-5) , yet a user can retroactively exempt their receipt from that loss.

### Impact Explanation
Direct theft / insolvency: the attacker withdraws 100% of principal while honest claimants of the same loss epoch receive only `lossRecoveryPrice`. The extra payout drains underlyings that were only funded to cover `pendingToFund`, so later claimants of the same epoch (and other funded receipts) find the vault short — the last claimants' transfers revert or the deficit is socialized onto active LPs. Loss magnitude: `attackerBasis * (1 - lossRecoveryPrice)`, i.e. up to the entire pending-loss share assigned to that epoch's receipts.

### Likelihood Explanation
Requires only an unprivileged tranche-token holder: (1) request withdraw in epoch N, (2) honest manager calls `stopEpochWithDuration(_lossAmount > 0)` realizing a partial loss, (3) attacker requests a second (even dust-sized) withdraw in epoch N+1 buffer — allowed since `requestWithdraw` has no "existing receipt" guard — (4) after the epoch rolls, `claimWithdrawRequest` pays both receipts at par. No privileged misbehavior needed; the only precondition is a realized stop-epoch loss while the attacker holds a pending receipt.

### Recommendation
Track the loss epoch per receipt instead of via the mutable `lastWithdrawRequest` pointer: iterate the user's per-epoch receipts (`withdrawsRequestsByEpoch`) checking `lossRecoveryPriceByEpoch` for each epoch with a nonzero entry, or store the user's loss-epoch separately (e.g., `lossEpochByUser`) that is only cleared by `_clearWithdrawClaimForEpoch`. Alternatively, block `requestWithdraw` while the user has an unclaimed loss-epoch receipt.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultLossEscape.t.sol — fork PoC sketch
// Setup: standard IdleCDOEpochVariant + IdleCreditVault, attacker holds AA tranches.

// 1. Epoch N buffer: attacker requests withdraw of `amount` tranche tokens.
cdoEpoch.requestWithdraw(amount, address(AAtranche));
uint256 lossEpoch = strategy.epochNumber(); // == lastWithdrawRequest[attacker]

// 2. Honest manager stops epoch N with a realized loss; borrower funds only part
//    of pendingWithdraws -> collectWithdrawFunds sets lossRecoveryPriceByEpoch[lossEpoch] < 1.
//    (via cdoEpoch.stopEpochWithDuration / loss path ending in strategy.collectWithdrawFunds)
assertLt(strategy.lossRecoveryPriceByEpoch(lossEpoch), RECOVERY_FULL);

// 3. New epoch buffer: attacker files a second (dust) withdraw request.
//    requestWithdraw overwrites lastWithdrawRequest[attacker] = lossEpoch + 1.
cdoEpoch.requestWithdraw(1, address(AAtranche));
assertEq(strategy.lastWithdrawRequest(attacker), lossEpoch + 1);

// 4. After epoch rolls (epochNumber > lastWithdrawRequest), claim.
vm.prank(attacker);
cdoEpoch.claimWithdrawRequest();

// _claimLossAdjustedWithdrawRequest checks lossRecoveryPriceByEpoch[lossEpoch+1] == 0 -> skips.
// _claimFundedWithdrawRequest pays full withdrawsRequests[attacker] at par.
uint256 paid = underlying.balanceOf(attacker) - balPre;
assertGt(paid, amount * strategy.lossRecoveryPriceByEpoch(lossEpoch) / RECOVERY_FULL);
// paid == full par amount -> attacker escaped the haircut; reserve is now short
// for the remaining loss-epoch claimants.
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-425)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L454-459)
```text
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
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
