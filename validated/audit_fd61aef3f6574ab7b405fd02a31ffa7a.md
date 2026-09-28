### Title
Permissionless `fullfillWriteOffRequest` lets any attacker frontrun the borrower and capture the negotiated write-off deal - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol])

### Summary
The external bug class is "an open, permissioned-in-spirit action can be frontrun by anyone watching the mempool, letting the attacker seize an asset intended for a specific counterparty and hold it for ransom." In idle-tranches, `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` is explicitly callable by any wallet and settles an off-chain-negotiated trade between a lender and the borrower. An attacker can observe the borrower's pending fulfill transaction, frontrun it, acquire the escrowed tranche tokens at the discounted price negotiated with the borrower, and thereby break the write-off deal — leaving the borrower's debt intact and holding tranche tokens that can either be ransom-sold back or redeemed at full `virtualPrice` through the normal withdraw flow.

### Finding Description
`createWriteOffRequest` escrows a user's tranche tokens along with an `underlyingsRequested` price that is the result of an off-chain agreement between the lender and the borrower [1](#0-0) . The intended flow is: borrower calls `fullfillWriteOffRequest`, receives the tranche tokens, then calls `IdleCDOEpochVariant.writeOffDeposit` to burn them and reduce `expectedEpochInterest`/NAV — i.e., the debt is written down [2](#0-1) .

`fullfillWriteOffRequest` has no caller check at all — the comment states "this function can be called by any wallet" [3](#0-2) . It only verifies that `_tranches`/`_underlyings` match the stored request, then transfers the tranche tokens to `msg.sender` [4](#0-3) . A test explicitly confirms a third-party buyer can fulfill and that a non-borrower fulfiller cannot then call `writeOffDeposit` [5](#0-4) .

Attack sequence (running epoch phase):

1. LP and borrower agree off-chain: LP escrows `T` tranche tokens requesting `U` underlyings, where `U` is typically below the tranches

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-102)
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
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-131)
```text
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L137-151)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L936-963)
```text
  function writeOffDeposit(uint256 _amount, address _tranche) external {
    // only borrower can call this method and only during an epoch, otherwise one should follow the traditional flow
    _checkNotAllowed(_borrower() != msg.sender || !isEpochRunning);
    _checkTranche(_tranche);
    // Remove raw donations so write-off interest math only sees accounted vault value.
    _skimDonatedAssets();

    // Calculate tranche tokens value with current tranche price
    uint256 _underlyings = _trancheToUnderlyings(_amount, _tranche);
    // We now calculate how much interest + fee the tranche would have generated in the current epoch
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    // given that _calcInterestWithdrawRequest returns an interest and diff value meant to be used in requestWithdraw
    // it does not include the buffer period but given that the epoch is running expectedEpochInterest was
    // calculated with the buffer period included, so we need to scale the interest
    uint256 _epochDuration = epochDuration;

    // (interest + diff) gives the interest based on tvl as write off debt won't follow senior/junior interest split ratio
    // diff is positive for AA and negative for BB (in this case it won't be > of interest)
    interest = uint256(int256(interest) + diff) * (_epochDuration + bufferPeriod) / _epochDuration;

    // Burn tranche tokens and decrease lastNAV
    _withdrawOps(_amount, _underlyings, _tranche);
    // Burn strategy tokens and decrease NAV (1:1 with underlyings)
    IdleCreditVault(strategy).burnStrategyTokens(_underlyings);

    // remove the interest + fee from expectedEpochInterest
    expectedEpochInterest -= interest;
  }
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L205-231)
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
```
