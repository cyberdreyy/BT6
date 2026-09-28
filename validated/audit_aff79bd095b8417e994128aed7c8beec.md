### Title
Write-off requests can be created with a zero `underlyingsRequested`, letting any fulfiller take escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` in `IdleCreditVaultWriteOffEscrow` validates that the deposited tranche amount is non-zero but performs no check on `underlyingsRequested`. Because `fullfillWriteOffRequest` only enforces `_underlyings >= currentRequest.underlyings`, a request stored with `underlyingsRequested == 0` can be fulfilled by any caller paying 0 underlying and receiving all of the escrowed tranche tokens. This mirrors the "zero bond" bug class: a parameter intended to set the economic floor of an action can silently be zero, removing the protection the flow is designed to provide.

### Finding Description
In `createWriteOffRequest`, only `amount` is checked:

```solidity
// cannot request write off with 0 tranche tokens
if (amount == 0) revert NotAllowed();
``` [1](#0-0) 

`underlyingsRequested` is accumulated into `userRequests[msg.sender].underlyings` and `pendingUnderlyings` with no minimum or non-zero requirement, even though it is the price the requester demands for their tranche tokens.

In `fullfillWriteOffRequest` the only price check is:

```solidity
if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
  revert WrongRequest();
}
``` [2](#0-1) 

When `currentRequest.underlyings == 0`, `_underlyings = 0` satisfies the check. The fulfiller then calls `safeTransferFrom(..., 0)` (a no-op), receives `_tranches` escrowed tranche tokens at line 151, and pays nothing. Requests can be created only while `isEpochRunning()` is true (line 88), i.e. when the tranche tokens still carry a positive claim on vault NAV via `virtualPrice`/`tranchePrice` in `IdleCDO`.

### Impact Explanation
Direct theft of the escrowed tranche tokens. A requester who creates a write-off request with `underlyingsRequested = 0` — e.g. a frontend bug, a misconstructed multicall, or a user intending to set the ask later (there is no update function, only `deleteWriteOffRequest`) — permanently loses the full claim value of those tranche tokens to whichever unprivileged fulfiller (any EOA, per the `@dev this function can be called by any wallet` comment) front-runs the fix. The fulfiller can hold the tranche tokens and redeem them through the normal withdraw-request path once the epoch stops, realizing value bounded only by the escrowed position's NAV.

### Likelihood Explanation
Requires a requester to create a request with a zero ask — a user/integrator error rather than something the attacker can force. However, the function signature makes it easy: `underlyingsRequested` is a free parameter with no validation, and there is no way to amend a request except deleting and recreating, which creates a window for an attacker to fulfill the zero-ask request first. Any MEV bot or observer can fulfill permissionlessly, so exploitation is guaranteed once such a request exists. Severity is bounded by the probability of integrator error, similar to the referenced finding where the bug was only latent because the sole caller passed a sane value.

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0`, so every escrowed tranche position has a non-zero minimum purchase price, mirroring the existing `amount == 0` check:

```solidity
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

Optionally also revert in `fullfillWriteOffRequest` if `_underlyings == 0` for defense in depth.

### Proof of Concept
Foundry fork PoC outline:

```solidity
// Epoch running; Alice holds 1000 AA tranche tokens worth ~1000 underlying.
vm.startPrank(alice);
AAtranche.approve(escrow, 1000e18);
// Mistakenly passes 0 as the ask
escrow.createWriteOffRequest(1000e18, 0);
vm.stopPrank();

// Attacker (any EOA) fulfills with 0 underlying
vm.prank(attacker);
escrow.fullfillWriteOffRequest(alice, 1000e18, 0);

assertEq(AAtranche.balanceOf(attacker), 1000e18); // got tranche tokens free
assertEq(underlying.balanceOf(alice), 0);        // paid nothing
// Attacker later requests withdrawal / redeems for ~full NAV share.
```

No privileged role, default, or freeze is needed; the only precondition is a zero-ask request, which the missing check permits.

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-155)
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

    // If the fulfiller is the borrower, they can then choose to either keep the tranche tokens or write them off via
    // IdleCDOEpochVariant.writeOffDeposit method.
  }
```
