### Title
Tranche holders can frontrun a privileged APR cut to lock a stale high rate into an irrevocable withdraw receipt — (`contracts/IdleCDOEpochVariant.sol`)

### Summary
`requestWithdraw` in `IdleCDOEpochVariant` converts a withdraw request into a fixed underlying-denominated receipt at request time: the receipt interest is computed from the strategy's *currently stored* APR via `_calcInterestWithdrawRequest`/`_calcInterest` (`_getStrategyApr()`), and then locked forever in `IdleCreditVault.requestWithdraw` while the tranche tokens are burned. Once the buffer period opens (`stopEpoch` sets `allowAAWithdrawRequest`/`allowBBWithdrawRequest` back to true), a KYC-passed tranche holder who observes a pending privileged APR reduction (`IdleCreditVault.setAprs`, or `setAprs` inside a `stopEpoch` call for the *next* buffer) can submit `requestWithdraw` first and permanently lock the old, higher rate, while the borrower obligation for the next epoch (`expectedEpochInterest`) is recomputed at `startEpoch` using the *new* APR.

### Finding Description
During the buffer period `requestWithdraw` executes:

- `_underlyings = principal + interest - totalFees` where `interest` is derived from the APR stored at that moment (`_calcInterestWithdrawRequest`, `contracts/IdleCDOEpochVariant.sol:856-878`).
- `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` fixes the payout (`contracts/IdleCDOEpochVariant.sol:788`), and `_withdrawOps` burns the tranche tokens, removing the principal from live NAV.

The only reconciliation for interest-rate drift is `interestForOverUnderPerformance += diff` (`contracts/IdleCDOEpochVariant.sol:784`), which at the next `startEpoch` is added to `adjustedActiveInterest` (`contracts/IdleCDOEpochVariant.sol:260`). For a BB withdrawer, `diff` is *negative*: `interestWithoutSplitRatio - _interest` where `_interest` (leveraged BB tranche share) exceeds the flat pro-rata amount. So the borrower is only charged the flat, non-split interest on the withdrawn principal, while the locked receipt pays the leveraged tranche share — both computed at the *old* APR. When the manager lowers the APR after the request, the flat contribution credited at `startEpoch` no longer matches what the epoch's interest (computed at the new APR) can sustainably fund; the gap is silently absorbed by remaining holders' NAV.

A symmetric positive-rate vector exists in `depositDuringEpoch` (`contracts/IdleCDOEpochVariant.sol:656-733`): shares are minted at `expectedFinal = _lastSavedNAV + trancheExpected`, which embeds only `expectedEpochInterest`. Any interest realized above expectation in `stopEpoch` (the `_interest` override path in `prepareStopEpochWithApr0`, `contracts/strategies/idle/IdleCreditVault.sol:513-530`) accrues pro-rata to shares priced on the lower expected figure, diluting existing holders — though this variant requires predicting repayment rather than pure mempool ordering, since `depositDuringEpoch` requires `block.timestamp < epochEndDate` while `stopEpoch` requires `block.timestamp >= epochEndDate`.

Existing guards do not stop the primary vector: `requestWithdraw` is intentionally open to any `isWalletAllowed` wallet during the buffer, `_skimDonatedAssets` and `_updateAccounting` only remove donations and crystallize already-realized losses, and `interestForOverUnderPerformance` was designed to neutralize withdrawals at a *constant* APR, not a mid-buffer APR change.

### Impact Explanation
A BB tranche holder who frontruns an APR decrease locks interest computed at the old APR into a receipt that the vault must pay in full. The shortfall equals approximately `(trancheShareBB − proRataShare) × amount × (epochDuration/(epochDuration+buffer))` priced at the stale APR, plus the `(oldApr − newApr)` rate differential on the withdrawn amount. Because `pendingWithdraws` is a hard claim paid by the borrower/recalled liquidity while `expectedEpochInterest` is recalculated at the reduced APR, the difference is socialized across all remaining tranche holders, i.e. direct yield theft bounded by the withdrawn amount and the APR delta.

### Likelihood Explanation
Low–Medium. It requires (a) a standard (non-prefunded, non-programmable) variant where buffer-period requests are enabled, (b) a privileged APR update that is observable before inclusion — either a public-mempool `setAprs`/`stopEpoch` transaction or a predictably scheduled rate change — and (c) a KYC'd tranche position. Rate cuts are a routine operational event, and requests are permissionless once the buffer opens, so the only real friction is timing.

### Recommendation
Compute withdraw-request interest from the APR that will govern the epoch in which the receipt is serviced (i.e. re-derive or clamp the locked interest at `stopEpoch`/`startEpoch` time rather than freezing it at request time), or snapshot `unscaledApr` at the moment the buffer opens and forbid `setAprs` while requests are outstanding for the pending epoch. Alternatively, charge the borrower the full locked tranche-share interest for early requests (not just the flat `interestWithoutSplitRatio`) so a stale-rate lock cannot shift cost onto remaining NAV.

### Proof of Concept
```solidity
// Fork test scaffolding mirrors test/foundry/IdleCreditVault.t.sol helpers:
// _stopCurrentEpochWithApr(apr) warps past epochEndDate and pranks manager stopEpoch.

function testFrontrunAprCutLocksStaleRate() external {
    _useStandardEpochVariant();              // non-AYS, non-programmable, instant disabled
    _stopCurrentEpochWithApr(10e18);         // epoch 0 ends at 10% APR -> buffer of epoch 1 opens

    // Two KYC'd BB depositors enter during the buffer and epoch 1 runs at 10%.
    uint256 amount = 100_000 * ONE_SCALE;
    uint256 trA = _depositWithUser(attacker, amount);   // BB tranche tokens
    uint256 trV = _depositWithUser(victim,   amount);
    vm.prank(manager); cdoEpoch.startEpoch();

    _stopCurrentEpochWithApr(10e18);         // epoch 1 ends -> buffer of epoch 2 opens, requests enabled

    // Manager broadcasts an APR cut for epoch 2: setAprs(5%) visible in mempool.
    // Attacker frontruns: locks interest computed at the OLD 10% APR.
    vm.prank(attacker);
    uint256 locked = cdoEpoch.requestWithdraw(trA, address(BBtranche));   // rate locked now

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 0); // APR cut lands after the request

    // Victim requests afterwards: locks interest at the NEW 5% APR.
    vm.prank(victim);
    uint256 lockedVictim = cdoEpoch.requestWithdraw(trV, address(BBtranche));

    assertGt(locked, lockedVictim, "attacker locked stale higher rate");

    // Epoch 2 runs at 5%. After it stops, both receipts must be paid;
    // the attacker's excess over the 5% rate is paid out of remaining NAV.
    vm.prank(manager); cdoEpoch.startEpoch();
    _stopEpochAndCheckPrices(2, 5e18, _expectedFundsEndEpoch());

    uint256 claimable = IdleCreditVault(address(strategy)).withdrawsRequests(attacker);
    // claimable exceeds what a fair 5% receipt would pay; the delta is
    // absorbed by the NAV of everyone who did not frontrun the cut.
}
```

Note: the PoC assumes `IdleCreditVault.setAprs` (or an equivalent APR update used by `stopEpoch`) is callable by owner/manager while the epoch is in the buffer phase; I could not fully verify an epoch-phase guard on `setAprs` within the available iterations. If `setAprs` is restricted to running-epoch-only and `stopEpoch` atomically sets the next APR before reopening requests, the mempool window narrows to frontrunning the `stopEpoch` transaction itself (requests are disabled during the running epoch, so ordering becomes: stopEpoch lands → buffer opens → any later APR change is frontrunnable). The `depositDuringEpoch` dilution vector is fully code-verified but requires predicting an above-expectation repayment rather than pure mempool ordering.