### Title
Missing epoch-state check in `fullfillWriteOffRequest` lets a fulfiller settle write-off requests at stale prices after the epoch ends - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` enforces that the epoch is running, but `fullfillWriteOffRequest` performs the same class of state transition — settling a request and releasing escrowed tranche tokens — with no check on the epoch phase. A fulfiller can wait for the epoch to stop, let tranche prices appreciate with the accrued epoch interest, and then fulfill the lender's stale fixed-price request, capturing the interest the lender earned in the meantime.

### Finding Description
`createWriteOffRequest` reverts with `EpochNotRunning` unless `IdleCDOEpochVariant.isEpochRunning()` is true (contracts/IdleCreditVaultWriteOffEscrow.sol:86-88). `fullfillWriteOffRequest` only checks that the request exists and that `_underlyings >= currentRequest.underlyings` (lines 123-135), then transfers underlyings minus `exitFee` to the lender and the escrowed `tranche` tokens to `msg.sender` (lines 137-151). There is no `isEpochRunning()` gate on the fulfillment path.

The lender prices the request in underlyings at request time, mid-epoch, when the tranche price reflects principal plus only accrued-to-date value. After `stopEpoch`, `_updateAccounting` accrues the full epoch interest into tranche prices (contracts/IdleCDOEpochVariant.sol:436). The request, however, still settles at the pre-stop nominal amount. Any unprivileged fulfiller (the intended buyer role, callable "by any wallet" per line 122) can therefore buy tranche tokens for less than their current redemption value.

Additionally, once the epoch stops, the intended borrower flow breaks: `writeOffDeposit` reverts unless `isEpochRunning` (contracts/IdleCDOEpochVariant.sol:938), so escrowed tranches fulfilled post-epoch cannot be written off — the exact state the escrow was designed for no longer exists, yet settlement still proceeds.

### Impact Explanation
Direct theft of unclaimed yield: the fulfiller pays the stale `currentRequest.underlyings` while receiving tranche tokens whose price already includes the epoch's accrued interest. The lender's loss equals `tranches * (priceAfter - priceAtRequest) / 1e18` minus nothing compensated — i.e., roughly the full epoch interest attributable to the escrowed tranche amount, plus any loss/reprice events in between. For a 30-day epoch at, e.g., 10% APR, the fulfiller captures ~0.8% of the escrowed position value per fulfillment, bounded only by the request size. The lender cannot react in the same transaction (fulfill is atomic), and `deleteWriteOffRequest` gives no protection against a fulfiller front-running a deletion or acting before the lender notices the epoch stopped.

### Likelihood Explanation
Medium. Requires a pending write-off request to survive an epoch boundary, which is realistic: lenders who list requests late in an epoch may not monitor the exact `stopEpoch` transaction. The attack needs only an unprivileged EOA with underlying tokens and no KYC requirement on the escrow itself (the escrow does not check `isWalletAllowed`). No privileged cooperation is needed — the honest owner/manager performs a routine `stopEpoch`, and the attacker fulfills afterward. The window is bounded by the lender's willingness to leave the request open, but nothing in the contract expires stale requests.

### Recommendation
Add the same epoch-state validation to `fullfillWriteOffRequest` used at creation:

```solidity
function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    ...
}
```

This also guarantees the borrower can subsequently call `writeOffDeposit` in the same epoch. Optionally, allow lenders to attach an expiry timestamp or require re-confirmation if the tranche price moved beyond a tolerance since `createWriteOffRequest`.

### Proof of Concept
Foundry fork sketch (extend the existing escrow/CreditVault test setup in `test/foundry/`):

```solidity
function testFulfillAfterEpochEnd() public {
    // lender deposits AA tranches during buffer, epoch starts via startEpoch()
    vm.prank(lender);
    escrow.createWriteOffRequest(trancheAmount, askUnderlyings); // priced mid-epoch

    // honest manager stops the epoch; interest accrues into tranche price
    vm.warp(epochEndDate + 1);
    vm.prank(manager);
    cdo.stopEpoch(newApr, 0);

    uint256 priceAfter = cdo.virtualPrice(address(AATranche));
    // attacker fulfiller settles stale request post-epoch
    deal(underlying, attacker, askUnderlyings);
    vm.startPrank(attacker);
    IERC20(underlying).approve(address(escrow), askUnderlyings);
    escrow.fullfillWriteOffRequest(lender, trancheAmount, askUnderlyings);
    vm.stopPrank();

    // attacker received tranches worth trancheAmount * priceAfter / 1e18
    // paid only askUnderlyings -> profit ~= accrued epoch interest on trancheAmount
    assertGt(trancheAmount * priceAfter / 1e18, askUnderlyings);
}
```

The revert expectation is that the call succeeds today; after the fix it must revert with `EpochNotRunning`.