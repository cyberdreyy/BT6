### Title
Owner/manager can reprice a running epoch mid-flight: `setTrancheAPRSplitRatio`, `setMinAprSplitAYS` and `IdleCreditVault.setApr` lack `isEpochRunning` guards - (contracts/IdleCDOCreditVault.sol:450-482, contracts/IdleCDO.sol:879-903, contracts/strategies/idle/IdleCreditVault.sol:225-235)

### Summary
The analog of "owner changes a bid parameter while an auction is ongoing" is a privileged parameter change while `isEpochRunning == true`. `IdleCDOEpochVariant` already enforces this invariant for some parameters — `setEpochParams` and `setIsProgrammableBorrower` revert when an epoch is live (`_checkNotAllowed(defaulted || isEpochRunning || ...)` at `contracts/IdleCDOEpochVariant.sol:122` and `:178`). But the parameters that determine how the already-fixed `expectedEpochInterest` is distributed — the AA/BB APR split (`trancheAPRSplitRatio`, `minAprSplitAYS`, `isAYSActive`) and the borrower APR (`IdleCreditVault.setApr` / `setAprs`) — can be changed at any point during a running epoch. An epoch is the analogue of the auction: lenders committed funds for a fixed term at a fixed expected yield split, and the split is only applied when `stopEpoch` calls `_updateAccounting`, i.e. using the *latest* parameter values rather than the ones in force when the epoch started.

### Finding Description
`expectedEpochInterest` is crystallized in `startEpoch` via `_calcInterest(getContractValue())` (`contracts/IdleCDOEpochVariant.sol:260-262`). From that moment, AA and BB holders have a claim on that interest whose allocation depends on the APR split configuration. That configuration is mutable mid-epoch:

- `setTrancheAPRSplitRatio` (`IdleCDO.sol:900-903` / `IdleCDOCreditVault.sol` equivalent) — only `onlyOwner` + bound check, no `isEpochRunning` check.
- `setMinAprSplitAYS` (`IdleCDOCreditVault.sol:479-482`) — same.
- `setIsAYSActive` (`IdleCDOCreditVault.sol:450-453`) — toggling AYS mid-epoch switches between fixed and adaptive split retroactively.
- `IdleCreditVault.setApr`/`setAprs` (`contracts/strategies/idle/IdleCreditVault.sol:206-235`) — callable by manager while the epoch is running; `lastApr` is read by mid-epoch paths (`depositDuringEpoch`, instant-withdraw APR-delta logic), so a change alters terms for positions and decisions already made under the old APR.
- `setInstantWithdrawParams` (`IdleCDOEpochVariant.sol:131-137`) is gated only on `paused()`, not on `isEpochRunning`, so `instantWithdrawAprDelta` and `instantWithdrawDelay` can change after `startEpoch` set `instantWithdrawDeadline = block.timestamp + instantWithdrawDelay` (`:268`) and after users submitted instant requests.

Concrete sequence mirroring the Nouns report:

1. Buffer phase: AA and BB lenders deposit; `trancheAPRSplitRatio` gives AA a minimum yield share.
2. `startEpoch()` runs — `isEpochRunning = true`, deposits pause, withdraw requests are disabled. Lenders are locked in.
3. Owner calls `setTrancheAPRSplitRatio` (or `setMinAprSplitAYS`, or disables AYS) mid-epoch.
4. `stopEpoch` → `_updateAccounting()` splits `expectedEpochInterest` between `lastNAVAA`/`lastNAVBB` using the *new* ratio, retroactively changing each tranche's claim on interest that accrued under the old terms.

### Impact Explanation
Direct redistribution of epoch yield between tranche classes. AA holders can lose their guaranteed minimum APR share (or, symmetrically, BB holders their leveraged upside) for an epoch whose length is arbitrary (`epochDuration`), so the quantifiable loss is bounded by the full `expectedEpochInterest` shift between the old and new split — potentially the entire epoch's junior-vs-senior differential, with no recourse since withdraw requests are disabled while the epoch runs. For `setApr`, mid-epoch `depositDuringEpoch` participants and the instant-withdraw APR-delta trigger are priced on `lastApr`, so an honest-but-routine APR update silently changes the economics of requests already queued.

### Likelihood Explanation
Requires only a normal admin action (parameter tuning is expected operationally — these setters exist to be called), sequenced while an epoch is running, which is the common state for a live pool. No malicious privileged role is needed; the bug is that the contract does not enforce the same epoch-state gating it already enforces for `setEpochParams` and `setIsProgrammableBorrower`. Likelihood is moderate since it depends on admin timing rather than attacker action.

### Recommendation
Apply the same guard used in `setEpochParams` to all epoch-affecting parameters:

```diff
// contracts/IdleCDO.sol / IdleCDOCreditVault.sol
function setTrancheAPRSplitRatio(uint256 _r) external virtual {
    _checkOnlyOwner();
+   _checkNotAllowed(isEpochRunning || defaulted);
    _checkAmountTooHigh((trancheAPRSplitRatio = _r) > FULL_ALLOC);
}
function setMinAprSplitAYS(uint256 _s) external virtual {
    _checkOnlyOwner();
+   _checkNotAllowed(isEpochRunning);
    _checkAmountTooHigh((minAprSplitAYS = _s) > FULL_ALLOC);
}
function setIsAYSActive(bool _a) external virtual {
    _checkOnlyOwner();
+   _checkNotAllowed(isEpochRunning);
    isAYSActive = _a;
}
```

And in `IdleCreditVault.setApr`, revert when `IIdleCDOEpochVariant(idleCDO).isEpochRunning()` for direct `setApr` calls (the CDO-internal scaled updates at start/stop should use a separate path), or snapshot the APR/split in `startEpoch` and use the snapshot for all mid-epoch and stop-time accounting. Snapshotting is the safer fix since it also protects `depositDuringEpoch` pricing.

### Proof of Concept
Foundry-style sketch extending `test/foundry/IdleCreditVault.t.sol`:

```solidity
function testMidEpochSplitRatioChangeRepricesTranches() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    // BB deposit to create junior buffer
    // ... deal + approve + depositBB ...

    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);
    cdoEpoch.startEpoch();          // epoch running, expectedEpochInterest fixed
    vm.stopPrank();

    uint256 priceAAExpected = cdoEpoch.virtualPrice(address(AAtranche));

    // Mid-epoch parameter change — currently succeeds, should revert
    vm.prank(owner);
    cdoEpoch.setTrancheAPRSplitRatio(FULL_ALLOC); // was e.g. 50_000

    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(10e18, 0);

    // AA price deviates from what depositors locked in at startEpoch:
    // interest that belonged to AA under the start-of-epoch ratio
    // is redistributed by the new ratio.
    assertLt(cdoEpoch.virtualPrice(address(AAtranche)), priceAAExpected);
}
```

Caveat: I verified the missing `isEpochRunning` guards on all listed setters and that `expectedEpochInterest` is fixed at `startEpoch`, but I did not trace the exact `_updateAccounting` split math line-by-line, so the magnitude of the AA↔BB transfer depends on the split-ratio application inside `_updateAccounting`/`_updateSplitRatio`, which should be confirmed when writing the full PoC.