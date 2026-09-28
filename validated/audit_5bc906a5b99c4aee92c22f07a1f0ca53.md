### Title
Loss-adjusted withdraw receipts are keyed to the post-increment epoch, so stopEpoch losses are never applied and pending receipts are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` stores the realized stop-epoch haircut under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` has already been incremented by `deposit()` earlier in the same `stopEpoch` call. Withdraw receipts, however, are keyed by `lastWithdrawRequest[_user]` — the pre-increment request epoch. `_claimLossAdjustedWithdrawRequest` therefore looks up a stale epoch index, finds `lossRecoveryPriceByEpoch[requestEpoch] == 0`, and falls through to `_claimFundedWithdrawRequest`, which pays the full basis at par even though the borrower only funded the haircut amount. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`IdleCreditVault.deposit()` increments `epochNumber` when invoked inside `stopEpoch` while `isEpochRunning()` is still true (the comment explicitly says "deposit done on stopEpoch (before setting the var to false)"). [3](#0-2)  In the same `stopEpochWithDuration`/`stopEpoch` flow, `collectWithdrawFunds` runs after that deposit, so when `_amount < pendingBasis` the haircut is written to `lossRecoveryPriceByEpoch[epochNumber]` — i.e. epoch `N+1`. [4](#0-3) 

But every receipt created during epoch `N` recorded `lastWithdrawRequest[_user] = N` and `withdrawsRequestsByEpoch[_user][N]`. [5](#0-4)  On claim, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` = `lossRecoveryPriceByEpoch[N]` = 0 and returns 0. [6](#0-5)  Execution then reaches `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` at par from strategy underlyings — even though `collectWithdrawFunds` only transferred `pendingToFund < pendingBasis` into the vault and zeroed `pendingWithdraws`. [7](#0-6) 

The secondary guard in `requestWithdraw` shares the same wrong key (`lossRecoveryPriceByEpoch[lossEpoch]` with `lossEpoch = lastWithdrawRequest`), so it also fails to detect the unclaimed loss-adjusted receipt and lets users stack new requests on top. [8](#0-7) 

### Impact Explanation
Direct theft plus permanent freezing. The strategy received only `pendingBasis − pendingLoss` underlyings for receipts that will be paid `pendingBasis` in aggregate. The first claimants withdraw at par and drain the vault's underlying balance; the last claimants' `_transferFundedClaim` either reverts on the `balance - reserve < _amount` guard or on the `safeTransfer` underflow, permanently freezing their funded receipts. [9](#0-8)  Quantified loss = `pendingLoss = _lossAmount * pendingBasis / totalBasis`, which can be the dominant share of TVL when pending withdrawals are large relative to active NAV (a stopEpoch loss is split pro rata by `previewLossAdjustedWithdrawFunds`). [10](#0-9)  The bug also silently defeats the loss-socialization design: pending receipt holders never take their assigned haircut, pushing the entire realized loss onto whoever claims last — an unprivileged ordering race among ordinary tranche holders, no privileged role required to trigger beyond the honest manager's `stopEpochWithDuration` call.

### Likelihood Explanation
Any epoch stopped with a non-zero `_lossAmount` while `pendingWithdraws != 0` triggers it — the loss path is explicitly supported (`previewLossAdjustedWithdrawFunds`, `stopEpochWithDuration`). An attacker only needs to be a tranche holder with a pending withdraw receipt (or observe a pending receipt and claim their own earlier funded receipt first). Because receipts pay strictly first-come-first-served at par while funding is haircut, every loss epoch creates a bank-run incentive; the theft is deterministic, not probabilistic.

### Recommendation
Store the loss recovery price under the epoch in which the receipts were requested, not the epoch counter after the stop-time increment. In `collectWithdrawFunds`, key `lossRecoveryPriceByEpoch` by `epochNumber - 1` (or snapshot the request epoch before `deposit()` bumps it), matching `lastWithdrawRequest`/`withdrawsRequestsByEpoch` semantics. Alternatively move the `epochNumber += 1` increment out of `deposit()` into an explicit post-collection step so all stop-time accounting (`collectWithdrawFunds`, `prepareStopEpochWithApr0`'s `apr0RateByEpoch[epochNumber]` at line 537, `instantWithdrawClaimsByEpoch`) uses a consistent epoch index.

### Proof of Concept
Foundry fork PoC (against the `IdleCreditVault.t.sol` harness style, pool in normal fixed-APR mode):

```solidity
function testLossEpochKeyMismatchPaysPar() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amountWei);          // attacker LP
    _depositWithUser(victim, amountWei, true);                // victim LP
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);                             // epoch 0 running

    // both request withdraw during epoch N (strategy.epochNumber() == 0)
    uint256 reqA = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    vm.prank(victim);
    uint256 reqV = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(victim), address(AAtranche));

    // borrower repays principal minus a loss; manager stops with _lossAmount > 0
    // -> IdleCDOEpochVariant calls strategy.deposit() (epochNumber -> 1)
    // -> collectWithdrawFunds(funded < pendingBasis)
    // stores lossRecoveryPriceByEpoch[1] = funded * 1e18 / pendingBasis
    _stopEpochWithLoss(lossAmount);

    assertEq(strategy.lossRecoveryPriceByEpoch(0), 0);        // receipt epoch has NO price
    assertGt(strategy.lossRecoveryPriceByEpoch(1), 0);        // price sits on wrong key

    // attacker claims: _claimLossAdjustedWithdrawRequest sees price==0 at epoch 0,
    // _claimFundedWithdrawRequest pays reqA at PAR (no haircut)
    uint256 balPre = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(address(this)) - balPre, reqA);

    // victim's identical receipt can no longer be paid: strategy balance <
    // remaining basis -> safeTransfer / reserve guard reverts -> frozen funds
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();
}
```

Expected observed behavior: `reqA` is paid in full although only `reqA * lossRecoveryPrice / 1e18` was funded for it, and the victim's claim reverts, confirming both the theft and the permanent freezing caused by the epoch-key mismatch.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-293)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L454-459)
```text
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
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
