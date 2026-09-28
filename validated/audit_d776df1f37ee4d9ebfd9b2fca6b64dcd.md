### Title
Zero-value write-off request lets any fulfiller seize escrowed tranches - (`contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary
`createWriteOffRequest` accepts a nonzero tranche deposit but allows `underlyingsRequested` to be zero. [1](#0-0)  `fullfillWriteOffRequest` then accepts zero underlying because it only rejects underpayment relative to the stored request. [2](#0-1) 

### Finding Description
A lender can create a write-off request with `underlyingsRequested == 0` while still escrow-depositing a nonzero amount of tranche tokens. [3](#0-2)  The fulfilment path treats that zero ask as valid because `_underlyings < currentRequest.underlyings` is false when both values are zero. [4](#0-3) 

The fulfiller pays no underlying, receives all escrowed tranche tokens, and the victim's request is deleted. [5](#0-4)  This violates the escrow invariant that tranche collateral can be released only in exchange for the consideration requested by its owner.

### Impact Explanation
Any unprivileged fulfiller can steal the complete tranche balance escrowed under a zero-priced request. [6](#0-5)  The loss is the full value of `currentRequest.tranches`, while the fulfiller spends no underlying.

### Likelihood Explanation
The exploit requires a lender to submit a request with a zero underlying ask, which can occur through malformed input, integration bugs, or a confusing front end. [3](#0-2)  Once such a request exists, fulfilment is permissionless and can be executed atomically by any address. [7](#0-6) 

### Recommendation
Reject zero-value write-off requests in `createWriteOffRequest` with `if (underlyingsRequested == 0) revert Is0();`. [3](#0-2)  Also enforce `_underlyings != 0` in `fullfillWriteOffRequest` as defense in depth for requests already stored by earlier deployments. [4](#0-3) 

### Proof of Concept
Add this test to the Foundry write-off-escrow fork fixture after the credit-vault epoch has been started:

```solidity
function testZeroAskWriteOffRequestStealsEscrowedTranches() external {
    uint256 trancheAmount = 100 * ONE_TRANCHE;

    // Victim creates a malformed request while the epoch is running.
    vm.startPrank(lender);
    tranche.approve(address(escrow), trancheAmount);
    escrow.createWriteOffRequest(trancheAmount, 0);
    vm.stopPrank();

    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);
    uint256 attackerTrancheBefore = tranche.balanceOf(attacker);

    // Any unprivileged fulfiller can take the tranches for zero underlying.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(lender, trancheAmount, 0);

    assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);
    assertEq(tranche.balanceOf(attacker), attackerTrancheBefore + trancheAmount);
    assertEq(tranche.balanceOf(address(escrow)), 0);
}
```

The calls map directly to the missing zero-value validation in `createWriteOffRequest` and the permissive equality check in `fullfillWriteOffRequest`. [1](#0-0) [2](#0-1)

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-151)
```text
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
