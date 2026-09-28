### Title
Stale `lastWithdrawRequest` epoch lookup skips loss-adjusted claims and pays haircutted receipts at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`_claimLossAdjustedWithdrawRequest` derives the claim epoch from the single scalar `lastWithdrawRequest[_user]` instead of iterating the user's actual per-epoch receipts. This mirrors the Stakehouse bug where a check was applied to the wrong (global/latest) token instead of the token being withdrawn: the lookup is applied to the *latest* request epoch rather than the epoch that actually carried the loss. A user who holds a loss-adjusted receipt from epoch N and then makes a new withdraw request in epoch M has `lastWithdrawRequest` overwritten to M, so the epoch-N haircut is never applied and the receipt is later paid at par through the funded-claim path — paying out more underlying than the borrower ever funded.

### Finding Description
`requestWithdraw` overwrites `lastWithdrawRequest[_user] = currentEpoch` on every request (`contracts/strategies/idle/IdleCreditVault.sol:282`). When `collectWithdrawFunds` receives less than `pendingWithdraws`, the shortfall is recorded as `lossRecoveryPriceByEpoch[epochNumber]` and `pendingWithdraws` is cleared (`lines 411-430`). At claim time, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`lines 789-801`). If the user re-requested in a later epoch, `lastWithdrawRequest` no longer equals the loss epoch, `lossRecoveryPrice == 0`, and the function returns 0 — the haircutted basis stays in `withdrawsRequestsByEpoch[_user][lossEpoch]` and in the aggregate `withdrawsRequests[_user]`.

Once `epochNumber > lastWithdrawRequest[_user]`, `_claimFundedWithdrawRequest` passes its gate (`lines 326-328`) and computes `amount = withdrawsRequests[_user] + apr0 buckets`, burning and paying the *full aggregate at par* via `_transferFundedClaim` (`lines 338-349`). The loss-adjusted component was only funded up to `claimBasis * lossRecoveryPrice`, so the user withdraws more underlying than the vault holds for them, draining funds reserved for other claimants.

### Impact Explanation
Direct overpayment from the vault's funded-claim balance: the attacker receives `claimBasis` instead of `claimBasis * lossRecoveryPrice / RECOVERY_FULL` for the loss epoch. The excess comes out of underlying earmarked for other users' pending claims, breaking the "one receipt one payout" and solvency invariants — later claimants' `_transferFundedClaim` calls revert or pay less. Quantified loss per attack: `claimBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL`.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`collectWithdrawFunds` partial funding event (honest manager/borrower action during a realized loss) while the attacker has a pending receipt, then a normal re-request in a subsequent buffer period. Both are unprivileged user actions in normal epoch phases; no privileged cooperation needed beyond routine epoch management.

### Recommendation
Track loss-adjusted epochs per user (e.g., iterate `withdrawsRequestsByEpoch` keys or store the loss epoch at request time) instead of deriving it from the mutable `lastWithdrawRequest`. Alternatively, subtract the haircutted basis from `withdrawsRequests[_user]` at `collectWithdrawFunds` time so the funded-claim path can never pay it at par.

### Proof of Concept
A Foundry fork PoC in `test/foundry/IdleCreditVault.t.sol` style: deposit, run epoch 0, request withdraw in epoch N; stop epoch with a loss so `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N] < RECOVERY_FULL`; in the next buffer period call `requestWithdraw` again (overwrites `lastWithdrawRequest` to M); run epoch M normally so it is fully funded; then `claimWithdrawRequest`. Assert the payout equals full `withdrawsRequests` including the epoch-N basis at par while the vault only holds `basis * lossRecoveryPrice` for it, leaving the second user's identical claim underfunded/reverting. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Caveat: I could not fully trace `_transferFundedClaim` (lines 897+) within the available iterations to confirm whether it caps the payout at the vault's funded balance; if it draws only from an exactly-funded reserve, the impact manifests as later claimants being frozen out rather than the attacker receiving extra tokens — still a valid insolvency/fair-payout break.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L279-294)
```text
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
