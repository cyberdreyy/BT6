### Title
Missing zero-amount validation on `underlyingsRequested` lets a fulfiller take a lender's escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` in `IdleCreditVaultWriteOffEscrow` validates `amount != 0` for the tranche tokens being escrowed, but never validates `underlyingsRequested`. A lender who deposits tranche tokens with `underlyingsRequested == 0` (e.g., a frontend/typo error, the exact class as the Footium `_message` report) creates a fulfillable request where the fulfiller can legally pass `_underlyings = 0`, receive all escrowed tranche tokens, and pay nothing. This mirrors the report's pattern: funds move without the economic term that was supposed to accompany them being enforced on-chain.

### Finding Description [1](#0-0) 

- `createWriteOffRequest` reverts only on `amount == 0` (line 90). `underlyingsRequested` is stored unchecked into `userRequests[msg.sender].underlyings` and `pendingUnderlyings`.
- `fullfillWriteOffRequest` only requires `_underlyings >= currentRequest.underlyings` (line 129). With `underlyings == 0`, a fulfiller passes `fullfillWriteOffRequest(user, trancheAmount, 0)`, pays zero underlying (and zero exit fee since `_totFee = 0`), and receives the full `_tranches` amount of tranche tokens at line 151. [2](#0-1) 

While a request is still pending, the lender can recover via `deleteWriteOffRequest`, but there is no protection once a fulfiller front-runs the deletion or the lender never notices. The escrow contract is exactly the "payment + identifying parameter" surface of the Footium report: the tranche deposit is the payment, `underlyingsRequested` is the term that must be non-empty/nonzero, and it is only enforced implicitly rather than on-chain.

### Impact Explanation
Permanent loss of the lender's escrowed tranche tokens with zero compensation. A fulfiller (or any EOA — the function is permissionless, `this function can be called by any wallet`) obtains `tranches` worth their full claim value in the credit vault for 0 underlying. The loss equals the escrowed tranche balance; for a lender writing off a large position this can be the entire escrowed amount.

### Likelihood Explanation
Requires lender input error (zero `underlyingsRequested`), same likelihood profile as the referenced finding — a frontend bug or malformed call. Monitoring bots can fulfill such requests atomically in the same block they appear, making recovery via `deleteWriteOffRequest` unreliable. Attacker is unprivileged; no trusted role is involved.

### Recommendation
Add a nonzero check mirroring the existing `amount` check:

```solidity
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

### Proof of Concept
Foundry-style PoC (vault in a running epoch, escrow initialized):

```solidity
// LP holds tranche tokens; epoch is running (isEpochRunning() == true)
uint256 trancheAmt = 10_000e18;

vm.startPrank(LP);
tranche.approve(address(escrow), trancheAmt);
// BUG: underlyingsRequested = 0 is accepted
escrow.createWriteOffRequest(trancheAmt, 0);
vm.stopPrank();

// Any unprivileged fulfiller takes all escrowed tranches paying nothing
uint256 balBefore = tranche.balanceOf(attacker);
vm.prank(attacker);
escrow.fullfillWriteOffRequest(LP, trancheAmt, 0); // no approval needed, _underlyings = 0

assertEq(tranche.balanceOf(attacker) - balBefore, trancheAmt); // attacker got all tranches for free
assertEq(underlying.balanceOf(LP), lpBalBefore); // LP received nothing
```

Note: this is self-inflicted-input validation rather than a directly attacker-triggered theft, matching the severity (Medium) and class of the source report. Whether it is "valid" under strict theft criteria is debatable — the lender must first err — but it is the strongest analog in this codebase to the "payment accepted with empty identifying/economic parameter" bug class.

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
