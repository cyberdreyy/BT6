### Title
Write-off requests can be fulfilled after their epoch availability expires - (`contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary
`IdleCreditVaultWriteOffEscrow` restricts `createWriteOffRequest` to running epochs, but `fullfillWriteOffRequest` does not enforce the same temporal boundary. An unprivileged buyer can therefore leave a lender’s stale request untouched until the epoch ends and tranche value has appreciated, then fulfill it at the old fixed asking price. This maps directly to the reported missing check that a requested time falls within the configured availability period.

### Finding Description
`createWriteOffRequest` reverts unless `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()` is true, so the active epoch is the request’s intended availability period. [1](#0-0)  The request stores the exact number of escrowed tranche tokens and fixed amount of underlyings requested. [2](#0-1) 

`fullfillWriteOffRequest`, however, checks only that the request exists, that `_tranches` equals the escrowed amount, and that `_underlyings` is at least the requested amount. [3](#0-2)  It does not check `isEpochRunning`, `epochEndDate`, or a stored expiry before deleting the request and transferring the tranche tokens to the fulfiller. [4](#0-3) 

The temporal restriction is material because `stopEpoch` accrues interest and updates tranche prices before reopening withdrawal requests. [5](#0-4)  The corresponding borrower write-off path is explicitly restricted to a running epoch, reinforcing that epoch activity is the relevant availability boundary. [6](#0-5) 

### Impact Explanation
A fulfiller can acquire escrowed tranche tokens at a price established before epoch interest was accrued. For example, if a lender escrows 10,000 tranche tokens for 10,000 underlying during an epoch and the tokens are worth 11,000 underlying after epoch settlement, the fulfiller pays only the stale 10,000 and receives the appreciated 10,000 tranche tokens, capturing approximately 1,000 underlying of value.

This is a direct value transfer from the requester to an unprivileged fulfiller. The broken invariant is the epoch-scoped availability of the write-off offer: a request that can only be created during an epoch remains executable outside that epoch even though tranche pricing and write-off semantics change when the epoch ends.

### Likelihood Explanation
The attack requires only an existing unfilled request and an arbitrary wallet with enough underlying to call `fullfillWriteOffRequest`; the function intentionally allows any caller. [7](#0-6)  No privileged action is required beyond the honest owner or manager stopping the epoch at its scheduled `epochEndDate`. [8](#0-7) 

Likelihood is limited by the lender’s ability to call `deleteWriteOffRequest` before fulfillment, but deletion does not retroactively expire a request and the fulfiller can act atomically immediately after the epoch transition. [9](#0-8) 

### Recommendation
Store the epoch number or explicit expiry timestamp in `WriteOffRequest`, and have `fullfillWriteOffRequest` reject fulfillment unless the epoch is still running and the request has not expired. A minimal fix is to add the same `!isEpochRunning()` check used by `createWriteOffRequest`, but storing the creation epoch is safer because it prevents fulfillment during a later epoch under the old terms.

### Proof of Concept
A Foundry PoC can extend `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`:

```solidity
function testFulfillWriteOffRequestAfterEpochExpires() external {
  uint256 requestedTranches = 10_000e18;
  uint256 requestedUnderlyings = 10_000e6;
  address buyer = makeAddr("buyer");

  // Epoch is running, so the LP creates an epoch-scoped offer.
  vm.prank(LP);
  escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

  // Honest manager ends the epoch and accrues interest.
  _stopCurrentEpoch();
  assertFalse(cdoEpoch.isEpochRunning());

  // The stale offer remains executable even though its availability period ended.
  deal(address(underlying), buyer, requestedUnderlyings);
  vm.startPrank(buyer);
  underlying.approve(address(escrow), requestedUnderlyings);
  escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
  vm.stopPrank();

  // Buyer received all escrowed tranche tokens at the stale fixed price.
  assertEq(tranche.balanceOf(buyer), requestedTranches);
}
```

Existing tests already establish that creation reverts after `_stopCurrentEpoch()` and that any third-party buyer can fulfill a request. [10](#0-9) [11](#0-10)  The missing assertion is that fulfillment itself reverts once `isEpochRunning()` becomes false.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-90)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L95-101)
```text
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L104-115)
```text
  /// @notice delete the write-off request and transfer tranche tokens back to the user
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L118-131)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L133-151)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L338-343)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
```

**File:** contracts/IdleCDOEpochVariant.sol (L435-483)
```text
      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L933-939)
```text
  /// @notice Write off the deposit, this will be used if the borrower and a lender comes to an off-chain agreement
  /// so here we burn tranche tokens, the equivalent amount of strategy tokens. Only the borrower can call this
  /// @param _amount Amount of tranche tokens to write off
  function writeOffDeposit(uint256 _amount, address _tranche) external {
    // only borrower can call this method and only during an epoch, otherwise one should follow the traditional flow
    _checkNotAllowed(_borrower() != msg.sender || !isEpochRunning);
    _checkTranche(_tranche);
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L142-145)
```text
    _stopCurrentEpoch();
    vm.expectRevert(abi.encodeWithSelector(EpochNotRunning.selector));
    escrow.createWriteOffRequest(0, 1e6);
  }
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L205-241)
```text
  function testFullfillWriteOffRequestAllowsThirdPartyBuyer() external {
    uint256 requestedTranches = 10000e18;
    uint256 requestedUnderlyings = 10000e6;
    address newBorrower = makeAddr("newBorrower");
    address buyer = makeAddr("buyer");

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    // rotate borrower after escrow initialization to prove fulfillment is independent from the borrower slot
    vm.prank(strategy.owner());
    strategy.setBorrower(newBorrower);

    deal(address(underlying), buyer, requestedUnderlyings);
    uint256 balPreBuyer = underlying.balanceOf(buyer);
    uint256 balPreBuyerTranche = tranche.balanceOf(buyer);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);
    uint256 contractValuePre = cdoEpoch.getContractValue();
    uint256 expectedEpochInterestPre = cdoEpoch.expectedEpochInterest();

    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.writeOffDeposit(requestedTranches, address(tranche));
    vm.stopPrank();

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0, 'write-off request tranches is not 0 after third-party fulfill');
    assertEq(underlyings, 0, 'write-off request underlyings is not 0 after third-party fulfill');
    assertEq(escrow.pendingUnderlyings(), 0, 'pending underlyings is not 0 after third-party fulfill');

    uint256 fee = requestedUnderlyings * escrow.exitFee() / escrow.FULL_VALUE();
    assertEq(balPreBuyer - underlying.balanceOf(buyer), requestedUnderlyings, 'buyer balance is wrong');
    assertEq(underlying.balanceOf(LP) - balPreLP, requestedUnderlyings - fee, 'LP balance is wrong after third-party fulfill');
    assertEq(tranche.balanceOf(buyer) - balPreBuyerTranche, requestedTranches, 'buyer tranche balance is wrong');
```
