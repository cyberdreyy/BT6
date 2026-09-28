### Title
Write-off request parameter updates are frontrunnable, letting a fulfiller fill a lender's escrowed tranches at stale, more favorable terms - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` mutates an existing request additively with no "expected current value" check, and `fullfillWriteOffRequest` can be called by any wallet. A lender who wants to change the price of their escrowed tranche tokens must update the request on-chain; an attacker can frontrun that update and fulfill the request at the old terms, buying the tranche tokens for fewer underlyings (or before the lender can cancel), exactly mirroring the ERC20 `approve()` race.

### Finding Description
A lender escrows tranche tokens via `createWriteOffRequest`, which deposits tranches and stores `WriteOffRequest{tranches, underlyings}` in `userRequests[msg.sender]` [1](#0-0) . To change the ask, the lender must call `createWriteOffRequest` again (raising `underlyings`) or `deleteWriteOffRequest` to exit [2](#0-1) .

`fullfillWriteOffRequest` is permissionless — "this function can be called by any wallet" — and only requires `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings` [3](#0-2) .

Race scenario:
1. During a running epoch, lender A creates a request: T tranche tokens for N underlyings (a distressed-debt sale).
2. Market moves / A reconsiders and sends `createWriteOffRequest(0... )` is not possible (`amount == 0` reverts) — so A must either deposit more tranches with a higher `underlyingsRequested` (which also adds tranches, so it can't just reprice), or send `deleteWriteOffRequest()`.
3. Attacker B sees A's `deleteWriteOffRequest` (or repricing tx) in the mempool and frontruns `fullfillWriteOffRequest(A, T, N)`. B pays only N underlyings (minus the 0.1% exit fee) and receives all T escrowed tranche tokens [4](#0-3) .
4. A's tx now reverts (`Is0()`), and A is forced into a sale at the stale price it was trying to cancel — B captured the spread between N and A's intended price. B can immediately redeem the tranches via the epoch withdrawal path, extracting real value from the vault's underlying.

Because `createWriteOffRequest` is additive-only and cannot reduce `tranches` or lower `underlyings`, the only "decrease allowance" equivalent is `deleteWriteOffRequest`, and that is precisely the tx that gets frontrun. There is no deadline, expected-old-value parameter, or two-step increase/decrease mechanism.

### Impact Explanation
Direct theft of value: the fulfiller buys escrowed tranche tokens at a price the lender explicitly attempted to revoke or raise. Loss equals the difference between the stale `underlyings` and the lender's intended new ask (or the fair value the lender was trying to avoid selling at). Bounded by the tranche position size; in a distressed vault this can be close to the tranche's full recovery value.

### Likelihood Explanation
Requires a pending write-off request plus a lender mempool-observable cancel/reprice while an epoch is running and the stale price is profitable for the fulfiller. On Ethereum mainnet this is standard mempool frontrunning; any unprivileged EOA can do it. Same external-requirement profile as the original approve-race finding (Medium).

### Recommendation
- Add an "expected current value" argument to `createWriteOffRequest` (revert if `userRequests[msg.sender]` differs), or provide `increaseUnderlyings`/`decreaseUnderlyings` functions.
- Alternatively, require the fulfiller to call `fullfillWriteOffRequest` only after the lender attests the terms, or add a lender-side `minUnderlyings`/expiry the fulfillment must satisfy, so a frontrun at stale terms cannot succeed after an update.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVaultWriteOffEscrow} from "contracts/IdleCreditVaultWriteOffEscrow.sol";
import {IdleCDOEpochVariant} from "contracts/IdleCDOEpochVariant.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";

contract WriteOffFrontrunTest is Test {
    // Fork mainnet at a block where an epoch is running for the target vault
    IdleCDOEpochVariant cdo = IdleCDOEpochVariant(0x_CDO_EPOCH);
    IdleCreditVaultWriteOffEscrow escrow = IdleCreditVaultWriteOffEscrow(0x_ESCROW);
    IERC20Detailed tranche = IERC20Detailed(0x_TRANCHE);
    IERC20Detailed underlying = IERC20Detailed(0x_UNDERLYING);

    address lender = address(0xA11CE);
    address frontrunner = address(0xB0B);

    function test_frontrunDelete() public {
        // lender deposits tranches and asks for N underlyings
        deal(address(tranche), lender, 100e18);
        deal(address(underlying), frontrunner, 1000e18);
        vm.startPrank(lender);
        tranche.approve(address(escrow), 100e18);
        escrow.createWriteOffRequest(100e18, 90e18); // sell 100 tranches for 90 underlying
        vm.stopPrank();

        // lender broadcasts deleteWriteOffRequest(); attacker frontruns with fulfill
        vm.startPrank(frontrunner);
        underlying.approve(address(escrow), 90e18);
        escrow.fullfillWriteOffRequest(lender, 100e18, 90e18);
        vm.stopPrank();

        // lender's cancel now reverts; tranches were sold at the stale price
        vm.expectRevert();
        vm.prank(lender);
        escrow.deleteWriteOffRequest();

        assertEq(tranche.balanceOf(frontrunner), 100e18);
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-116)
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
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-135)
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
