### Title
Unfunded instant-withdraw receipts are paid at par from the pooled vault balance, letting instant claimants drain funds reserved for haircutted/funded withdraw claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` reads the aggregate receipt counter `instantWithdrawsRequests[_user]` — which accumulates receipts across epochs regardless of whether the borrower ever funded them — and pays the full amount out of the strategy's underlying balance via `_transferFundedClaim`. Funding status is tracked only by the separate aggregate `pendingInstantWithdraws`, which is decremented in `collectInstantWithdrawFunds` but is never consulted on the claim path. The analog of a buffer over-read is therefore a "state over-read": the claim logic treats the entire receipt balance as funded, including the unfunded (out-of-bounds) portion. [1](#0-0) [2](#0-1) 

### Finding Description
The withdraw-receipt design deliberately records two views of the same liability: per-user/per-epoch receipt basis (`instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch`) and a funded-cash view (`pendingInstantWithdraws`, reduced only when the IdleCDO actually pulls underlyings from the borrower in `collectInstantWithdrawFunds`). Normal withdrawals learned this lesson: `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` when the borrower under-funds, and claims are haircutted through `_claimLossAdjustedWithdrawRequest`. Instant receipts have no equivalent haircut path — `previewLossAdjustedWithdrawFunds` splits a realized loss only between `pendingWithdraws` (normal receipts) and active LPs, never touching `pendingInstantWithdraws`. [3](#0-2) [4](#0-3) 

Concretely, in `claimInstantWithdrawRequest` the vault:

1. reads `amount = instantWithdrawsRequests[_user]` (all epochs, funded or not),
2. burns the receipt tokens and zeroes the counter,
3. calls `_transferFundedClaim(_user, amount)`, whose only solvency check is that the transfer does not dip into `defaultRecoveryReserve`. [5](#0-4) 

There is no check that `collectInstantWithdrawFunds` was ever called for the user's request epoch, and no per-epoch recovery price for instant receipts. An attacker who requested an instant withdrawal in an epoch where the borrower repaid less than the full instant bucket — e.g. a `stopEpochWithDuration(_lossAmount)` epoch where the loss waterfall ignores instant receipts, or a partial repayment — still claims 100% of the receipt. The payout is sourced from the strategy's underlying balance, which is the same pot that backs funded normal receipts awaiting `_transferFundedClaim`, so the unfunded instant claim is paid by other users' funded claims until the balance is exhausted.

### Impact Explanation
Direct theft / unfair loss socialization: the first instant claimant to call `claimWithdrawRequest`/`claimInstantWithdrawRequest` after an under-funded epoch receives par value while later normal-receipt claimants either take the `lossRecoveryPriceByEpoch` haircut or find the vault balance depleted (their claims revert on insufficient balance). Loss equals the unfunded portion of `instantWithdrawsRequests`, bounded by the strategy's underlying balance net of `defaultRecoveryReserve`. This breaks the "one receipt, one (funded) payout" and loss-waterfall invariants: instant receipts escape haircuts that the code explicitly applies to normal receipts. [6](#0-5) 

### Likelihood Explanation
Requires an epoch where instant withdrawals are requested (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`, attacker-controlled only in the sense that the attacker submits a normal `requestWithdraw` when the condition holds) and the borrower under-funds the instant bucket. Under-funding is reachable whenever the borrower repays less than owed — including the managed `stopEpochWithDuration` loss path, which computes `pendingToFund`/`activeLoss` only over `pendingWithdraws` and the active basis and contains no branch for `pendingInstantWithdraws`. Since `claimInstantWithdrawRequest` has no funding check, no privileged misbehavior is needed beyond the honest manager/borrower executing a loss epoch while instant receipts are pending; an attacker needs only a whitelisted/KYC'd position and a timely claim. I could not fully verify every ordering constraint in `stopEpochWithDuration`/`collectInstantWithdrawFunds` (e.g. whether a nonzero `pendingInstantWithdraws` blocks the loss path), so the exact reachable phase should be confirmed in the PoC; if under-funding instant receipts is unreachable in any phase, the finding collapses. [7](#0-6) 

### Recommendation
Track instant-withdraw funding symmetrically to normal withdrawals: either record a per-epoch recovery price for instant receipts (extend `lossRecoveryPriceByEpoch`/add an `instantRecoveryPriceByEpoch` populated in `collectInstantWithdrawFunds` when `_amount < pendingInstantWithdraws`), or gate `claimInstantWithdrawRequest` on the user's receipt being funded — e.g. revert while `pendingInstantWithdraws != 0` for the corresponding epoch or cap the payout to the funded remainder. At minimum, assert in `claimInstantWithdrawRequest` that the claimed amount does not exceed the vault's funded (non-reserve) instant bucket.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testInstantWithdrawOverclaimOnUnderfundedEpoch() external {
    // 1. user1 deposits AA during buffer; attacker (user2) also deposits.
    // 2. manager startEpoch; manager sets a lower unscaledApr so instant path triggers.
    // 3. user2 calls cdoEpoch.requestWithdraw(0, AATranche) -> routed to
    //    creditVault.requestInstantWithdraw; instantWithdrawsRequests[user2] = X,
    //    pendingInstantWithdraws = X.
    // 4. Drive stopEpoch such that borrower under-funds (stopEpochWithDuration loss,
    //    or simply do not transfer full instant amount to the vault); show
    //    pendingInstantWithdraws remains > 0 and no haircut is recorded for user2.
    // 5. Fund the vault partially (e.g. funded normal receipts of user1 sit in the vault).
    // 6. user2 calls cdoEpoch.claimInstantWithdrawRequest():
    //    assert underlying.balanceOf(user2) increase == X (par payout), while
    //    user1's claimWithdrawRequest() now reverts / pays less than its funded basis.
}
```

Note: step 4's exact mechanics depend on whether the CDO's loss path permits nonzero `pendingInstantWithdraws`; I was unable to verify that branch in the available iterations, so the PoC premise (an epoch that ends with unfunded instant receipts while the vault holds other claimable underlyings) needs confirmation before this finding is final.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L786-801)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L761-769)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
```
