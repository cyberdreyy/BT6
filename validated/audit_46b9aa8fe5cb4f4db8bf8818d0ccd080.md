### Title
Write-off requests with `underlyingsRequested == 0` let any fulfiller steal escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` validates that `amount != 0` but never validates `underlyingsRequested`. A request stored with `underlyings == 0` satisfies `fullfillWriteOffRequest`'s payment check with `_underlyings == 0`, allowing any unprivileged caller to claim the victim's entire escrowed tranche balance without paying anything — mirroring the missing `weight > 0` check in `setStrategy` that let zero-weight elements silently pass validation.

### Finding Description
The external report describes a setter that checks an aggregate invariant (weights sum to 100%) while omitting a per-element nonzero check, letting a zero-weight element pass and corrupt downstream accounting. The same pattern exists in `IdleCreditVaultWriteOffEscrow`:

`createWriteOffRequest` checks `amount == 0` and reverts, but stores `underlyingsRequested` unvalidated — it can be `0` (or accumulate to `0` for legacy requests) [1](#0-0) .

`fullfillWriteOffRequest` is explicitly callable by anyone ("this function can be called by any wallet") and only requires `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings`. For a request with `underlyings == 0`, passing `_underlyings == 0` satisfies both checks [2](#0-1) .

The function then pulls 0 underlyings, deletes the request, and transfers all `currentRequest.tranches` tranche tokens to `msg.sender` [3](#0-2) .

### Impact Explanation
Direct theft of user funds by an unprivileged attacker. Any lender who creates a request with `underlyingsRequested = 0` — a plausible mistake (e.g., UI default, or a lender signalling "free write-off") — has their entire escrowed tranche position seized by the first caller of `fullfillWriteOffRequest` at zero cost. Loss equals the full `tranches` amount escrowed; the attacker can then redeem or write off the tranches. Broken invariant: one escrowed receipt corresponds to one negotiated payout — here a payout of zero is accepted.

### Likelihood Explanation
Requires a lender to submit `underlyingsRequested == 0`. Unlike the privileged-manager misconfiguration in the original report, the exploitable state here is created by an unprivileged user, and exploitation is fully permissionless, atomic, and front-runnable by any observer of `createWriteOffRequest` transactions. `EpochNotRunning` gate only restricts creation to running epochs; fulfillment has no epoch gate, so a stale zero request remains exploitable even after the epoch stops.

### Recommendation
Add a nonzero check in `createWriteOffRequest`:
```solidity
if (underlyingsRequested == 0) revert NotAllowed();
```
Optionally also enforce `currentRequest.underlyings > 0` in `fullfillWriteOffRequest` to protect already-created zero-underlying requests.

### Proof of Concept
```solidity
// Foundry fork PoC sketch
// Setup: epoch running (isEpochRunning() == true), lender holds AA/BB tranche tokens.

// 1. Lender (mistakenly) creates a request asking for 0 underlyings
vm.prank(lender);
tranche.approve(address(escrow), lenderTrancheBal);
vm.prank(lender);
escrow.createWriteOffRequest(lenderTrancheBal, 0); // succeeds: no zero-check on underlyingsRequested

// 2. Attacker (any EOA) fulfills with 0 underlyings and receives all tranches
vm.prank(attacker);
escrow.fullfillWriteOffRequest(lender, lenderTrancheBal, 0); // WrongRequest check passes

// 3. Assertions
assertEq(tranche.balanceOf(attacker), lenderTrancheBal); // attacker stole all tranches
assertEq(underlying.balanceOf(attacker), attackerBalBefore); // paid nothing
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-131)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L135-151)
```text
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
