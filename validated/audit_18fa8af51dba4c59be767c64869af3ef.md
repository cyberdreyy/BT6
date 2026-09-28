### Title
Stale `lastWithdrawRequest` epoch lets a loss-adjusted withdraw receipt be claimed at par — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` only looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — i.e., a single "pointer" to the user's most recent request epoch. If a user's earlier loss-adjusted receipt is left unclaimed and the user files a new `requestWithdraw` in a later, fully-funded epoch, the haircutted basis of the old epoch stays inside `withdrawsRequests[_user]` and is paid out at par by `_claimFundedWithdrawRequest`. This mirrors CVE-2023-2177's class: a stale/freed ledger entry (`withdrawsRequestsByEpoch[user][lossEpoch]`) is later dereferenced through the wrong accessor, breaking the "one receipt, haircutted payout" invariant and overpaying the attacker.

### Finding Description
- In `requestWithdraw`, the strategy adds the new principal to both the aggregate `withdrawsRequests[_user]` and the per-epoch `withdrawsRequestsByEpoch[_user][currentEpoch]`, and overwrites `lastWithdrawRequest[_user] = currentEpoch` without first settling or segregating earlier loss-adjusted epochs. [1](#0-0) 
- When the borrower short-funds an epoch, `collectWithdrawFunds` sets `pendingWithdraws = 0` and records `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (the haircut), transferring only the haircutted amount of underlyings into the strategy. [2](#0-1) 
- At claim time, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads only `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. Because `lastWithdrawRequest` now points to the newer (non-loss) epoch, the lookup returns 0 and the function exits without clearing `withdrawsRequestsByEpoch[_user][lossEpoch]`. [3](#0-2) 
- `_claimFundedWithdrawRequest` then computes `amount = withdrawsRequests[_user] + apr0...` — still containing the old loss-epoch basis — burns the receipt tokens and pays it at par via `_transferFundedClaim`. [4](#0-3) 
- The attacker therefore receives `oldBasis` instead of `oldBasis * lossRecoveryPrice / RECOVERY_FULL`. The excess `(1 - lossRecoveryPrice/RECOVERY_FULL) * oldBasis` is paid out of underlyings reserved for other claimants/LPs — a direct theft of yield/principal, not merely a revert.

### Impact Explanation
Quantified loss per attack: `oldBasis * (RECOVERY_FULL - lossRecoveryPrice) / RECOVERY_FULL` underlyings stolen from the strategy's claim reserve. Example: 100,000 USDC receipt in a loss epoch funded at 60% recovery → attacker withdraws 100,000 USDC instead of 60,000, extracting 40,000 USDC that belongs to other pending claimants or active LPs (insolvency at the margin: last claimers' transfers revert on insufficient balance, i.e., permanent freezing of their unclaimed funds).

### Likelihood Explanation
The attacker is an unprivileged KYC'd tranche holder performing only normal calls: `requestWithdraw` in a loss epoch, refrain from claiming, `requestWithdraw` again in a later epoch, then `claimWithdrawRequest`. All timing is driven by honest manager/borrower actions (`stopEpochWithDuration` partial funding, epoch rollovers). No guard blocks this: the "wait one epoch" check at line 326 is satisfied for the new epoch, `_settleApr0` doesn't touch normal receipts, and nothing forces per-epoch clearing. Likelihood hinges on a partial-loss `stopEpochWithDuration` occurring — within scope per `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds`.

### Recommendation
In `requestWithdraw`, either (a) force-settle any outstanding loss-adjusted epoch for `_user` before accepting the new request (invoke the `_claimLossAdjustedWithdrawRequest` path for all epochs with `lossRecoveryPriceByEpoch != 0`), or (b) store a per-user list of pending request epochs and iterate it in `claimWithdrawRequest` instead of keying loss recovery off the single `lastWithdrawRequest[_user]` value. At minimum, `_claimLossAdjustedWithdrawRequest` should check every epoch entry of the user, not only the latest.

### Proof of Concept
Foundry fork scenario (against `test/foundry/IdleCreditVault.t.sol` harness):
```solidity
// 1. Attacker deposits in epoch N (KYC'd wallet, AA or BB tranche), epoch starts.
// 2. Attacker calls cdo.requestWithdraw(amount) -> receipt minted, withdrawsRequestsByEpoch[user][N]=amount.
// 3. Borrower short-funds: manager calls stopEpochWithDuration with _lossAmount > 0 such that
//    collectWithdrawFunds receives < pendingBasis -> lossRecoveryPriceByEpoch[N] = 60% (example).
// 4. Epoch N+1 runs. Attacker does NOT claim. Attacker deposits fresh tranche tokens and calls
//    requestWithdraw(amount2) -> lastWithdrawRequest[user] = N+1, withdrawsRequests[user] = amount + amount2.
// 5. Epoch N+1 stops fully funded (collectWithdrawFunds with >= pendingBasis).
// 6. Attacker calls cdo.claimWithdrawRequest():
//    - _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N+1] == 0 -> returns 0.
//    - _claimFundedWithdrawRequest: epochNumber > N+1 gate passes; pays withdrawsRequests[user] at par.
// assertEq(received, amount + amount2) instead of amount*0.6 + amount2.
// Excess = amount * 0.4 is taken from the strategy's funded reserve; a subsequent
// honest claimer's _transferFundedClaim reverts on insufficient underlying balance.
``` [5](#0-4) [6](#0-5)

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
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
