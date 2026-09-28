### Title
Closed-pool withdrawal receipts are minted without claim accounting, permanently freezing user funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
When a pool is closed through `stopEpoch(..., 1)`, `epochEndDate` becomes zero, but users can still submit withdrawal requests for their remaining tranche tokens. `IdleCreditVault.requestWithdraw` burns the CDO’s principal and mints a receipt to the user, yet deliberately skips both `pendingWithdraws` and `withdrawsRequests` accounting in closed-pool mode. The resulting receipt has no recorded claim, so `claimWithdrawRequest` pays zero and leaves the receipt outstanding.

### Finding Description
The bug is a withdrawal-accounting mismatch analogous to CVE-2017-10920’s inconsistent mapping and unmapping counts.

After a successful close-pool stop, `epochEndDate` is set to zero. [1](#0-0)  In `IdleCreditVault.requestWithdraw`, the contract detects this closed state and still burns `_principal` from the CDO and mints `_amount` strategy tokens to the requester as a receipt. [2](#0-1)  However, because `isClosed` is true, it skips incrementing `pendingWithdraws`, and because `unscaledApr == 0 && !isClosed` is false, it also skips `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]`. [3](#0-2) 

`claimWithdrawRequest` then reaches `_claimFundedWithdrawRequest`, whose payout consists only of `withdrawsRequests[_user]`, settled APR0 principal, open APR0 principal, and APR0 interest. [4](#0-3)  Since the closed-pool receipt was never written into any of those structures, the claim amount is zero, `_burn(_user, 0)` destroys nothing, and `_transferFundedClaim(_user, 0)` transfers nothing. [5](#0-4) 

The broken invariant is “one receipt, one payout”: the strategy minted a claim token to the user, but no storage slot records the corresponding withdrawal liability.

### Impact Explanation
Every tranche holder who follows the supported post-close redemption path permanently loses access to the requested underlying. The CDO’s strategy-token principal is already burned and the user receives only an unusable strategy-token receipt, while the recalled underlying remains in `IdleCreditVault` without a claim entry.

The loss is quantified by the receipt amount returned from `IdleCDOEpochVariant.requestWithdraw`; for example, a 10,000-underlying withdrawal request freezes 10,000 underlying. Repeated `claimWithdrawRequest` calls do not remedy the issue because the missing accounting is persistent rather than epoch-dependent.

### Likelihood Explanation
The trigger does not require privileged misconduct. An honest manager can close the pool using the documented `_interest == 1` flow, after which any ordinary tranche-token holder naturally calls `requestWithdraw` to redeem. The CDO explicitly intends users in this phase to request withdrawals and claim immediately, as shown by the close-path comments and state changes. [6](#0-5) 

Existing protections do not prevent the mismatch. `requestWithdraw` remains allowed, `claimWithdrawRequest` bypasses the epoch wait when `epochEndDate == 0`, and no reserve or post-default path records the closed-pool receipt. [7](#0-6) 

### Recommendation
Record closed-pool receipts in a claimable accounting bucket even though no future `stopEpoch` will fund them. The simplest correction is to still execute `withdrawsRequests[_user] += _amount` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` when `isClosed` is true, while continuing to exclude `_amount` from `pendingWithdraws`.

Alternatively, introduce a dedicated `closedPoolRequests` or post-close receipt mapping and pay it from `_transferFundedClaim`. The implementation must ensure that closed-pool receipts cannot be double-counted as borrower-facing `pendingWithdraws` and cannot consume any finalized default-recovery reserve.

### Proof of Concept
The following Foundry test uses the existing `IdleCreditVault.t.sol` fixture and demonstrates that a post-close request mints an unclaimable receipt:

```solidity
function testClosedPoolWithdrawReceiptIsNotClaimable() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address user = makeAddr("closedPoolWithdrawUser");

    _depositWithUser(user, amount, true);
    _startEpochAndCheckPrices(0);

    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1); // close pool: recalls all principal

    assertEq(cdoEpoch.epochEndDate(), 0);
    assertTrue(cdoEpoch.allowAAWithdrawRequest());

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));

    vm.prank(user);
    uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

    assertGt(receipt, 0);
    assertEq(strategyToken.balanceOf(user), receipt);

    // The strategy minted a receipt but recorded no claim.
    assertEq(creditVault.withdrawsRequests(user), 0);
    assertEq(creditVault.pendingWithdraws(), 0);
    assertEq(creditVault.postDefaultRequests(user), 0);

    uint256 balanceBefore = underlying.balanceOf(user);

    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();

    // Claim succeeds without paying or burning the minted receipt.
    assertEq(underlying.balanceOf(user), balanceBefore);
    assertEq(strategyToken.balanceOf(user), receipt);

    // A retry cannot resolve the missing accounting.
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest();
    assertEq(underlying.balanceOf(user), balanceBefore);
    assertEq(strategyToken.balanceOf(user), receipt);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L484-493)
```text
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-275)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L276-294)
```text
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
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
