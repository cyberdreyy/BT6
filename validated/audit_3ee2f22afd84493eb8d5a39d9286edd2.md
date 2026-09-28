### Title
Write-off escrow offers never expire and can be exercised at stale terms after tranche value changes - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow` lets a lender park tranche tokens in a standing sell offer (`userRequests`) that any fulfiller — most notably the borrower — can execute at any later time by calling `fullfillWriteOffRequest`. The offer has no expiry, no epoch binding, and is fulfillable in any protocol state (buffer, running, stopped, closed, even after default). This mirrors the unrevocable-consent bug class: a commitment made under one set of conditions remains executable forever under arbitrarily different ones, letting the counterparty pick the moment that maximally disadvantages the offer maker.

### Finding Description
- `createWriteOffRequest(amount, underlyingsRequested)` escrows the lender's tranche tokens and records a fixed `underlyings` price. It is only callable while `isEpochRunning()`, so the price is set against the tranche value known at that time [1](#0-0) .
- `fullfillWriteOffRequest(_user, _tranches, _underlyings)` has no phase or time gating whatsoever: it only checks `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings`, then pays the user the stale `underlyings` amount (minus `exitFee`) and hands the tranches to the fulfiller [2](#0-1) .
- Nothing invalidates or reprices the offer across `startEpoch`/`stopEpoch` transitions, interest accrual, pool close, or default/finalization. The only escape is the user proactively calling `deleteWriteOffRequest` [3](#0-2) . There is no expiry and no requirement that fulfillment happen in the same epoch the consent was given.
- The request can only grow monotonically: a user wanting to *raise* their asking price on the same tranches cannot edit `underlyings` downward; they must notice the drift and explicitly delete/recreate [4](#0-3) .

### Impact Explanation
A fulfiller (any EOA, including the borrower or a third-party speculator — an unprivileged actor) can keep a lender's stale offer in their pocket and execute it exactly when tranche appreciation makes it maximally profitable:

- A lender escrows `T` tranches requesting `U` underlyings while the pool is running and `virtualPrice(tranche) ≈ U/T`.
- The epoch continues to accrue borrower interest; after `stopEpoch` repayments the tranche `virtualPrice` rises, e.g. `T` tranches are now worth `U' > U`.
- The fulfiller calls `fullfillWriteOffRequest` paying only `U` (plus the ≤1% `exitFee`) and receives tranches redeemable for `U'`, extracting `U' − U` of value that belongs to the lender's appreciated position.
- Symmetrically, after a borrower default tranche value collapses to the recovery price; if a lender created a request at par pricing (`underlyings ≈ T * oldPrice`), the request cannot hurt the fulfiller, but a lender who had priced *low* (distressed sale) while a later partial recovery or `finalizeDefaultRecovery` over-funding lifts `virtualPrice` can likewise be filled at the stale low price.

The lender's loss is bounded by their escrowed tranche value but is entirely avoidable only if they front-run the fulfiller with `deleteWriteOffRequest` — a race the original consent semantics never contemplated.

### Likelihood Explanation
- Requires only that a lender leave a write-off request open across an epoch boundary while tranche value moves — a common pattern since write-off requests are inherently long-lived (they exist to wait for the borrower to buy back distressed debt).
- No privileged action is needed by the attacker; `fullfillWriteOffRequest` is permissionless by design (`@dev this function can be called by any wallet`).
- The extracted value equals tranche appreciation since request creation, which in a yield-bearing credit pool trends upward each epoch — so the longer the offer sits, the more skewed it becomes in the fulfiller's favor.
- Moderated by the lender's ability to cancel, which is why this is medium rather than high severity: the vulnerability is the absence of any expiry/epoch-scoping on a standing commitment, not the impossibility of revocation.

### Recommendation
Bind write-off consent to the conditions under which it was given:

1. Record `epochNumber` (or `isEpochRunning` epoch index) at `createWriteOffRequest` and require fulfillment in the same epoch, or stamp the request with the tranche `virtualPrice`/timestamp and auto-expire offers after a configurable TTL or epoch transition.
2. Alternatively, let `fullfillWriteOffRequest` enforce a freshness check (e.g. revert if `IdleCDOEpochVariant(idleCDOEpoch).epochNumber` advanced since creation), forcing the lender to re-confirm pricing under the new NAV.
3. Optionally allow `createWriteOffRequest`/`deleteWriteOffRequest` semantics to support repricing (set `underlyings` on existing escrowed tranches without full withdrawal), so lenders can update consent without withdrawing tokens.

### Proof of Concept
Reproducible Foundry fork PoC sketch:

```solidity
// Assume: running epoch on IdleCDOEpochVariant, AA write-off escrow deployed,
// lender holds AA tranches, fulfiller is an arbitrary EOA.
function testStaleWriteOffOfferSnipedAfterAppreciation() external {
    uint256 trancheAmt = 100e18;
    uint256 priceAtRequest = cdoEpoch.virtualPrice(address(AATranche)); // e.g. ~1.0
    uint256 ask = trancheAmt * priceAtRequest / 1e18;                 // ~100 USDC

    // 1. Lender creates standing offer while epoch is running
    vm.startPrank(lender);
    AATranche.approve(address(escrow), trancheAmt);
    escrow.createWriteOffRequest(trancheAmt, ask);
    vm.stopPrank();

    // 2. Epoch ends, borrower repays principal + interest; tranche price rises
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(address(underlying), borrower, expectedInterest);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, expectedInterest);

    uint256 priceNow = cdoEpoch.virtualPrice(address(AATranche));
    assertGt(priceNow, priceAtRequest); // tranches appreciated

    // 3. Unprivileged fulfiller exercises the stale offer months later
    uint256 lenderBalPre = underlying.balanceOf(lender);
    deal(address(underlying), fulfiller, ask);
    vm.startPrank(fulfiller);
    underlying.approve(address(escrow), ask);
    escrow.fullfillWriteOffRequest(lender, trancheAmt, ask);
    vm.stopPrank();

    // 4. Fulfiller now holds tranches worth more than what lender received
    assertEq(AATranche.balanceOf(fulfiller), trancheAmt);
    uint256 extracted = trancheAmt * priceNow / 1e18 - ask; // minus exitFee
    assertGt(extracted, 0); // value drained from lender's appreciated position
    assertEq(underlying.balanceOf(lender) - lenderBalPre, ask * (FULL_VALUE - exitFee) / FULL_VALUE);
}
```

Steps 1–3 require only ordinary user actions (`createWriteOffRequest`, public `fullfillWriteOffRequest`) and honest privileged calls (`stopEpoch`), satisfying the unprivileged-attacker constraint; the broken invariant is that consent terms fixed at request time remain executable under materially different valuations with no expiry or epoch scoping.

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
