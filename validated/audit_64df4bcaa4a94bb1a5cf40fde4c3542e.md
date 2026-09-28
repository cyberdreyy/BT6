### Title
`fullfillWriteOffRequest` lets anyone consume another user's write-off request and seize their escrowed tranche tokens when the requested underlyings is 0 — (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
The CrabNetting bug class — a permissionless state-mutating function that invalidates another user's pending action — maps onto `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest`. The function is callable by any wallet and permanently `delete`s `userRequests[_user]` (the analog of marking someone else's nonce used). The only protection is a minimum-payment check, but `createWriteOffRequest` permits `underlyingsRequested == 0`, in which case the check passes with `_underlyings == 0` and the fulfiller receives the victim's escrowed tranche tokens for free.

### Finding Description
`createWriteOffRequest` only requires `amount != 0`; `underlyingsRequested` is unchecked, so a lender can hold escrowed tranche tokens with `underlyings == 0` (e.g., as a placeholder request, a front-end default, or a partially-created request where only `amount` was incremented on a second call). [1](#0-0) 

`fullfillWriteOffRequest` then enforces only `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings`. With `underlyings == 0`, an attacker calls `fullfillWriteOffRequest(victim, tranches, 0)`: the request is deleted, `_underlyings - _totFee == 0` is "paid" to the victim, and the attacker receives the full escrowed `tranches`. [2](#0-1) 

### Impact Explanation
Direct theft: the victim loses all escrowed tranche tokens and receives nothing; the request cannot be recovered since `userRequests` is deleted. The tranche tokens carry redemption value in the credit vault (or can be written off by the borrower), so the attacker captures real value at zero cost.

### Likelihood Explanation
Requires a victim request with `underlyings == 0`. `createWriteOffRequest` exposes this parameter freely and nothing in the UI-facing contract prevents a zero value; a lender topping up tranche amounts (`currentRequest.tranches + amount`) may leave `underlyingsRequested` at 0 intending to set it later. The attack is a single permissionless call during the running epoch, executable by any EOA.

### Recommendation
- Revert in `createWriteOffRequest` when `underlyingsRequested == 0` (or require `underlyings > 0` for a request to be fulfillable).
- Additionally/ alternatively, let the fulfiller be restricted or let the lender specify a minimum price per tranche, and treat `underlyings == 0` requests as unfulfillable drafts.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// 1. Epoch running: lender creates a request with underlyingsRequested = 0
vm.prank(lender);
tranche.approve(address(escrow), amount);
vm.prank(lender);
escrow.createWriteOffRequest(amount, 0); // succeeds, no zero-check

// 2. Attacker fulfils it, paying nothing
vm.prank(attacker);
escrow.fullfillWriteOffRequest(lender, amount, 0);

// 3. Assertions
assertEq(tranche.balanceOf(attacker), amount);  // attacker got victim's tranches
assertEq(underlying.balanceOf(lender), balBefore); // lender paid 0
(WriteOffRequest r) = escrow.userRequests(lender);
assertEq(r.tranches, 0); // request permanently deleted
```

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-152)
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
