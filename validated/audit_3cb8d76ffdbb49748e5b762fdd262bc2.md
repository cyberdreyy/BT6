### Title
`deleteWriteOffRequest` is vulnerable to frontrunning by `fullfillWriteOffRequest`, locking the seller into a stale price - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
A lender (LP) creates a write-off request in `IdleCreditVaultWriteOffEscrow` by depositing tranche tokens and specifying the amount of underlying they demand. Any third party (or the borrower) can fulfill the request by paying exactly that amount and taking the tranches. The seller can cancel via `deleteWriteOffRequest`, but there is no expiry and no freshness check on the stored price, so a fulfiller can frontrun a pending `deleteWriteOffRequest` transaction and force the sale at a price the seller no longer wants — the exact `Cooler.rescind`/`clear` pattern.

### Finding Description
`createWriteOffRequest` escrows the seller's tranche tokens and records `tranches`/`underlyings` in `userRequests` [1](#0-0) . `deleteWriteOffRequest` returns the tranches only if the request still exists [2](#0-1) . `fullfillWriteOffRequest` is callable by any wallet and settles atomically whenever the stored terms match [3](#0-2) .

Because `deleteWriteOffRequest` and `fullfillWriteOffRequest` race on the same storage slot, an attacker watching the mempool can fulfill the request in the same block the seller broadcasts a delete, making the delete revert (`Is0()` since `userRequests[_user]` is already cleared). The terms are fixed at request creation; there is no expiry, oracle bound, or cooldown after which a request becomes unfillable.

This matters because tranche token value is not static: `virtualPrice` on `IdleCDOEpochVariant` drifts with accrued interest, and — more sharply — losses from `stopEpochWithDuration(_lossAmount)`, `_handleBorrowerDefault`, or finalized default recoveries haircut tranche value discontinuously. A seller who priced their tranches pre-loss (or simply at a stale market price) and tries to cancel after learning new information is forced to sell at the stale price.

### Impact Explanation
Loss of funds for the seller. The attacker pays `currentRequest.underlyings` and receives `currentRequest.tranches` [4](#0-3) ; if the true value of those tranches exceeds the paid underlying (e.g., the seller under-priced before a positive repricing, or wants to keep the tranches after conditions improve), the difference is captured by the frontrunner. More concretely for this protocol: a seller who listed tranches during a running epoch and then observes an imminent epoch event that makes their ask unfavorable cannot cancel in time — the sale executes against their will. The loss is bounded by `requested underlyings` vs. tranche fair value, and is a direct transfer of value from seller to fulfiller.

### Likelihood Explanation
Medium. It requires (a) an open write-off request, (b) the seller attempting to cancel (which is exactly what `deleteWriteOffRequest` exists for), and (c) a mempool observer willing to pay the ask. Generalized frontrunning bots and the borrower itself satisfy (c). Requests sit open across an entire running epoch (`createWriteOffRequest` requires `isEpochRunning()` [5](#0-4) ), giving a long window where the market/haircut can move against the seller while the request remains fillable. No existing guard (nonReentrant, `WrongRequest` param check, KYC — note fulfill has no `isWalletAllowed` check) prevents the race; `WrongRequest` only protects against underpayment, not timing.

### Recommendation
Add an expiry timestamp to each `WriteOffRequest` (e.g., `uint40 expiry`), set by the seller at creation and enforced in `fullfillWriteOffRequest` (`block.timestamp <= currentRequest.expiry`), after which only `deleteWriteOffRequest` succeeds. Alternatively, bound fulfillment to the current tranche `virtualPrice` from `IdleCDOEpochVariant` (e.g., reject fulfillment if the implied price deviates more than a seller-specified tolerance from `virtualPrice(tranche)`), which directly mirrors the Cooler fix suggestion of an oracle-based price bound.

### Proof of Concept
Foundry fork scenario against the deployed escrow (mirroring `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` helpers):

```solidity
function testFrontrunDeleteWriteOffRequest() external {
    // epoch running, LP already holds tranches
    uint256 reqTranches = 10000e18;
    uint256 reqUnderlyings = 10000e6;

    vm.prank(LP);
    escrow.createWriteOffRequest(reqTranches, reqUnderlyings);

    // Market moves / new info: LP wants out and broadcasts deleteWriteOffRequest.
    // Attacker sees it in mempool and frontruns with fulfill in the same block.
    address attacker = makeAddr("attacker");
    deal(address(underlying), attacker, reqUnderlyings);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), reqUnderlyings);
    escrow.fullfillWriteOffRequest(LP, reqTranches, reqUnderlyings); // frontrun
    vm.stopPrank();

    // LP's delete now reverts: request already consumed
    vm.expectRevert(Is0.selector);
    vm.prank(LP);
    escrow.deleteWriteOffRequest();

    // attacker holds the tranches; LP forced to accept the stale price (minus fee)
    assertEq(tranche.balanceOf(attacker), reqTranches);
    (uint256 t,) = escrow.userRequests(LP);
    assertEq(t, 0);
}
```

Sequence: epoch running → LP `createWriteOffRequest` → conditions change → LP broadcasts `deleteWriteOffRequest` → attacker frontruns `fullfillWriteOffRequest` → LP's delete reverts on `Is0()` [6](#0-5) , sale settles at the stale terms.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L87-88)
```text
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L93-101)
```text
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-115)
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
