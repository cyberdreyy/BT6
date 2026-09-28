### Title
Stale write-off orders remain fulfillable forever — no expiry/epoch check on `fullfillWriteOffRequest` lets a fulfiller execute out-of-date quotes against escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` may only be called while an epoch is running (`IdleCreditVaultWriteOffEscrow.sol:88`), which scopes the negotiated quote to the conditions of that epoch (current tranche NAV, APR, solvency). However `fullfillWriteOffRequest` (`IdleCreditVaultWriteOffEscrow.sol:123`) performs no epoch, timestamp, or validity check at all: a request created in epoch N can be fulfilled in epoch N+k, during a buffer period, after a loss-bearing `stopEpochWithDuration`, after pool close, or after a borrower default and `finalizeDefaultRecovery`. The attacker is the third-party fulfiller (explicitly permissionless — "this function can be called by any wallet", `:122`), who chooses the moment of execution after the seller's fixed `underlyings` quote has become favorable to the buyer.

### Finding Description
The write-off flow works as a standing limit order:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol:123-151
function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    WriteOffRequest memory currentRequest = userRequests[_user];
    if (currentRequest.tranches == 0) revert Is0();
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }
    ...
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);  // tranches to fulfiller
}
```

The check only enforces exact tranche amount and a minimum underlying amount — both fixed at creation time. Nothing ties the request to the epoch in which it was created. Two stale-state execution paths exist:

1. **NAV appreciation**: tranche price (`virtualPrice`/`tranchePrice` via `_updateAccounting`) rises each epoch as interest accrues. A request quoting `underlyings` priced at epoch-N NAV becomes increasingly cheap for the fulfiller; they buy `currentRequest.tranches` at the stale price and pocket `currentNAV − underlyings − exitFee`.
2. **State changes**: requests survive borrower default (`defaulted`, `finalizeDefaultRecovery`), pool close (`epochEndDate == 0`, `epochDuration == 0`), and emergency shutdown — contexts in which the symmetric privileged path `writeOffDeposit` (`IdleCDOEpochVariant.sol:936-938`) is deliberately disabled (`!isEpochRunning` revert). The escrow never re-checks `isEpochRunning` or `defaulted`, so quotes negotiated under a solvent running epoch settle under completely different recovery/haircut conditions.

The seller can technically cancel via `deleteWriteOffRequest`, but there is no enforced validity window: the protocol's own design implies epoch-scoped quotes (creation is epoch-gated), yet execution ignores that scope entirely.

### Impact Explanation
A fulfiller extracts `trancheNAV_now − underlyingsRequested − exitFee` from the seller's escrowed position by timing fulfillment across epoch boundaries. With epochs of weeks duration and positive APR, the drift per epoch is `underlyings * apr * epochDuration / 365d`; a 10% APR epoch on a 100k USDC-quote order yields roughly 1–2k USDC of extractable value per unclaimed epoch, compounding. Impact is bounded by the seller's failure to cancel, so the loss is contingent rather than guaranteed, but it is a direct transfer of lender value to a permissionless actor exploiting an expired-in-spirit quote.

### Likelihood Explanation
Requires (a) an open write-off request and (b) tranche NAV drift or a default/close state change before fulfillment. Write-off escrows are an opt-in periphery for distressed exits, so open orders may sit for extended periods — precisely the condition under which staleness accrues. The fulfiller needs only underlying approval; no KYC gating applies to `fullfillWriteOffRequest` (tests confirm non-Keyring buyers can fulfill, `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw`). Likelihood is moderate-low due to dependence on an uncancelled resting order.

### Recommendation
Bind fulfillment to the same validity window as creation: record `epochNumber`/`epochEndDate` (or a `block.timestamp` expiry / the epoch in which `createWriteOffRequest` was called) in `WriteOffRequest` and revert in `fullfillWriteOffRequest` if the epoch has rolled, the pool defaulted, or the deadline passed. Alternatively require `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()` in `fullfillWriteOffRequest` so quotes can only execute under the epoch conditions they were priced for.

### Proof of Concept
```solidity
// Foundry fork test, pattern after test/foundry/IdleCreditVaultWriteOffEscrow.t.sol
function testStaleWriteOffOrderFulfilledAfterNavDrift() external {
    uint256 trancheAmt = 10_000e18;
    uint256 quote = 10_000e6; // priced at ~1.0 virtualPrice in epoch N

    vm.prank(LP);
    escrow.createWriteOffRequest(trancheAmt, quote); // only possible while running

    // roll several epochs; tranche price accrues interest each stopEpoch
    for (uint i; i < 3; ++i) {
        _stopCurrentEpochWithApr(10e18); // buffer
        vm.prank(manager);
        cdoEpoch.startEpoch();
    }
    // epoch N+k running again; NAV per tranche now > 1.0
    uint256 navNow = cdoEpoch.virtualPrice(address(tranche)) * trancheAmt / 1e18;
    assertGt(navNow, quote);

    address buyer = makeAddr("buyer");
    deal(address(underlying), buyer, quote);
    vm.startPrank(buyer);
    underlying.approve(address(escrow), quote);
    escrow.fullfillWriteOffRequest(LP, trancheAmt, quote); // succeeds: no expiry/epoch check
    vm.stopPrank();
    // buyer now holds trancheAmt worth navNow > quote
}
```

Confidence note: the missing expiry/epoch check is verified directly in `IdleCreditVaultWriteOffEscrow.sol:123-155`; whether a permanently-open order is judged a vulnerability or an accepted standing-order design depends on protocol intent, but the asymmetric gating (epoch-gated creation, ungated execution, and `writeOffDeposit` explicitly epoch-locked) indicates the quote was meant to be epoch-scoped.