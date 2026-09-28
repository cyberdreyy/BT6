### Title
Front-running `createWriteOffRequest`/`deleteWriteOffRequest` lets a fulfiller consume a stale write-off offer — ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow` stores a lender's standing offer (`userRequests[_user] = {tranches, underlyings}`) that any unprivileged wallet can fill via `fullfillWriteOffRequest`. The lender can only change or cancel that offer with a separate transaction (`createWriteOffRequest` to amend, `deleteWriteOffRequest` to cancel). This is the same approve()/transferFrom race class as `updateCommitment()`/`acceptCommitment()`: a fulfiller can front-run the lender's update/cancel and fill the offer at the old terms, so the lender is forced to sell tranche tokens at a price they were actively trying to move away from.

### Finding Description
`fullfillWriteOffRequest` requires `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings`, then atomically pays `underlyings - fee` to the lender and hands the escrowed tranche tokens to the fulfiller [1](#0-0) . There is no nonce, deadline, or update-and-fill atomicity: `createWriteOffRequest` merely accumulates onto `userRequests[msg.sender]` and `deleteWriteOffRequest` clears it [2](#0-1) . A pending fulfill transaction therefore executes against the pre-update request whenever it lands first.

Concretely, mirroring the reported scenario: a lender posts a request selling `T` tranche tokens for `U` underlyings while the epoch runs. Tranche value rises (virtualPrice accrual, or post-request information about borrower repayment), so the lender broadcasts either (a) `createWriteOffRequest` to raise `underlyings`, or (b) `deleteWriteOffRequest` to cancel entirely. A fulfiller sees the mempool transaction and front-runs it with `fullfillWriteOffRequest(user, T, U)`, acquiring `T` tranche tokens for the stale, lower price `U`. The lender's amendment/cancel then reverts (deleted request → `Is0()`, or `tranches != _tranches`), but the fill is already done.

### Impact Explanation
Direct, quantified economic loss for the lender equal to `max(0, T * virtualPrice - U)` — the spread between the tranche tokens' current redemption value and the stale ask — transferred to an unprivileged fulfiller. Because the escrowed tranches remain claimable on the vault (or later default recovery), the fulfiller captures real vault value, not just a price opinion. This violates the intended-consent invariant: the lender's update transaction was meant to make the old offer unavailable, exactly as the Teller lender's `updateCommitment` was meant to shrink `maxPrincipal`.

### Likelihood Explanation
The offer is a public, persistent on-chain order; monitoring `userRequests` and mempool transactions is trivial. Any EOA can call `fullfillWriteOffRequest` (the function is explicitly permissionless, `nonReentrant` only). No existing guard stops it: `createWriteOffRequest` is only gated by `isEpochRunning()` [3](#0-2)  and there is no cancel-then-check delay, no expiry, and no `requestId`/version check in the fulfill path. Likelihood is bounded by the economic condition that the offer becomes mispriced relative to tranche value — common during APR/price drift across an epoch or when the lender updates an under-priced ask.

### Recommendation
Bind the fill to the offer version the fulfiller saw, e.g. require the fulfiller to pass both expected `underlyings` and a request nonce/timestamp incremented on every `createWriteOffRequest`/`deleteWriteOffRequest`, reverting on mismatch; alternatively add a lender-controlled `cancelWriteOffRequest` that is processed before any fill in the same block (e.g., via a short timelock/fill-delay), or let the lender update via a single `updateWriteOffRequest(newTranches, newUnderlyings)` that invalidates prior terms atomically. At minimum, document that creates/cancels are front-runnable.

### Proof of Concept
Foundry fork PoC (anvil/local, contracts as deployed):

```solidity
// Setup: epoch running, lender holds AA tranche tokens, escrow initialized.
uint256 T = 100e18;
uint256 U = 50e6; // underlyings asked (6 decimals)

// 1. Lender creates the write-off request
vm.prank(lender);
escrow.createWriteOffRequest(T, U); // deposits T tranches, asks U underlyings

// 2. Tranche value drifts up (or lender wants a higher ask).
//    Lender broadcasts a second createWriteOffRequest to raise `underlyings`
//    (or deleteWriteOffRequest to cancel). Tx sits in mempool.

// 3. Fulfiller (any EOA) front-runs it and fills at the STALE terms
uint256 trancheBefore = tranche.balanceOf(fulfiller);
vm.prank(fulfiller);
escrow.fullfillWriteOffRequest(lender, T, U);

// 4. Lender's update/cancel now reverts
vm.expectRevert(Is0.selector);
vm.prank(lender);
escrow.deleteWriteOffRequest(); // request already consumed

// Assertions
assertEq(tranche.balanceOf(fulfiller) - trancheBefore, T);
// lender received U - fee instead of the new (higher) ask;
// loss = T * cdoEpoch.virtualPrice(tranche)/1e18 - U, captured by fulfiller.
```

Run: `forge test --match-test testFrontRunWriteOffFill -vv` in `test/foundry/` on a forked mainnet state where an epoch is running.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-116)
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
