### Title
Missing `underlyingsRequested` validation lets a fulfiller seize escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates `amount != 0` but never validates `underlyingsRequested`. A write-off request created with `underlyingsRequested == 0` (or while an accumulated request's tranche/underlying ratio is lopsided) can be fulfilled by any wallet — including the borrower — paying zero underlyings and receiving all of the lender's escrowed tranche tokens. The borrower can then burn those tranches via `writeOffDeposit` to erase real debt for free.

### Finding Description
`createWriteOffRequest` only rejects `amount == 0`; any `underlyingsRequested`, including `0`, is accepted: [1](#0-0) 

`fullfillWriteOffRequest` only requires that the fulfiller pays *at least* `currentRequest.underlyings` and buys *exactly* `currentRequest.tranches`. When `currentRequest.underlyings == 0`, passing `_underlyings = 0` satisfies `_underlyings < currentRequest.underlyings == false`, so the check passes and the fulfiller receives the lender's tranche tokens while transferring nothing (the fee on `0` is also `0`): [2](#0-1) 

Additionally, requests accumulate via `currentRequest.underlyings + underlyingsRequested` (line 99) with no cap or consistency check against `tranches`, so a single accidental `underlyingsRequested = 0` call permanently skews the request terms and there is no way to amend a request — only `deleteWriteOffRequest`, which creates a race that a fulfiller bot can front-run.

This mirrors the external bug class: a numeric input argument (`underlyingsRequested`, analogous to `ragged_rank`) is consumed without validation, and the invalid value propagates into the request-matching logic to produce an exploitable state.

### Impact Explanation
Direct theft. Any unprivileged EOA (or the borrower) monitoring the mempool/contracts can call `fullfillWriteOffRequest(victim, victimRequest.tranches, 0)` and receive all escrowed tranche tokens for free. Loss = the full underlying value of the lender's escrowed tranche position at the current tranche price. If the borrower fulfills, they additionally convert the stolen tranches into a free debt write-off via `IdleCDOEpochVariant.writeOffDeposit`, socializing the loss to remaining tranche holders. The escrow contract holds tranche tokens with no epoch gating on `fullfillWriteOffRequest`, so the theft window persists as long as the request exists.

### Likelihood Explanation
Requires a lender to submit `underlyingsRequested == 0` (or a request whose aggregate underlyings the fulfiller considers cheap relative to tranche value). A `0` ask is plausible via UI defaulting, fat-finger, or a lender who intends "any offer" semantics — the contract's "overpay allowed, underpay reverts" design actively invites low-ball fulfillment. The fulfiller attack is permissionless, atomic, and front-runnable before `deleteWriteOffRequest` can execute, so once a zero-ask request exists the theft is near-certain. Medium likelihood, high impact on the affected lender.

### Recommendation
In `createWriteOffRequest`, revert when `underlyingsRequested == 0` (extend the existing `if (amount == 0) revert NotAllowed();` check to cover both parameters). Optionally also enforce a sanity bound (e.g., `underlyingsRequested <= amount * tranchePrice / ONE_TRANCHE` or a configurable max) and/or provide an `updateWriteOffRequest` path so lenders can fix terms without a delete/recreate race.

### Proof of Concept
Foundry test against `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` setup (existing fixtures: `cdoEpoch`, `escrow`, `AAtranche`, `underlying`, `borrower`):

```solidity
function testPocZeroAskWriteOffTheft() external {
    // epoch must be running (createWriteOffRequest requires isEpochRunning)
    _startEpochAndCheckPrices(0);

    address lender = makeAddr("lender");
    address thief  = makeAddr("thief");

    // lender holds AA tranche tokens
    uint256 trancheAmt = 1_000e18;
    deal(address(AAtranche), lender, trancheAmt);

    // lender creates a write-off request forgetting to set underlyingsRequested (0)
    vm.startPrank(lender);
    AAtranche.approve(address(escrow), trancheAmt);
    escrow.createWriteOffRequest(trancheAmt, 0); // no validation rejects this
    vm.stopPrank();

    assertEq(AAtranche.balanceOf(address(escrow)), trancheAmt);

    // any wallet fulfills the request paying 0 underlyings
    vm.prank(thief);
    escrow.fullfillWriteOffRequest(lender, trancheAmt, 0);

    // thief received all tranche tokens, lender received nothing
    assertEq(AAtranche.balanceOf(thief), trancheAmt);
    assertEq(underlying.balanceOf(lender), 0);
    assertEq(escrow.userRequests(lender).tranches, 0); // request consumed
}
```

The same PoC works with `thief == borrower`, after which the borrower calls `cdoEpoch.writeOffDeposit(...)` to burn the stolen tranches and extinguish real debt at zero cost.

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
