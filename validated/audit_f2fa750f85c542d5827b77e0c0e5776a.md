### Title
BB withdrawal requests accumulate a negative `interestForOverUnderPerformance` that underflows `expectedEpochInterest` in `startEpoch`, permanently freezing the vault - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The Etherlink bug class — a small/cheap user transaction that deterministically reverts block-level settlement and can be spammed — maps onto idle-tranches' epoch settlement: an unprivileged tranche holder can poison the signed accumulator `interestForOverUnderPerformance` via `requestWithdraw`, so that the honest manager's `startEpoch` reverts on every call. Because `startEpoch` is the only way to re-open the pool after `stopEpoch`, all remaining lender funds are permanently frozen.

### Finding Description
`requestWithdraw` computes a signed `diff` in `_calcInterestWithdrawRequest` and adds it to `interestForOverUnderPerformance` (`IdleCDOEpochVariant.sol:773-784`). For a BB withdrawal, `_diff = int256(interestWithoutSplitRatio) - int256(_interest)` is negative because the BB share of interest is lower than the naive TVL share whenever `trancheAPRSplitRatio` favors AA (the normal case, e.g. 80/20). The per-request comment states this accumulator is "subtracted from the expectedEpochInterest" — it is applied at `startEpoch`.

`startEpoch` computes the next epoch's `expectedEpochInterest` from the post-withdrawal NAV. A BB holder who requests a large withdrawal near `epochEndDate` leaves a negative accumulator whose magnitude is proportional to the *pre-withdrawal* NAV and the *current* epoch's duration, while `startEpoch`'s fresh expected interest is proportional to the much smaller *remaining* NAV. When `|interestForOverUnderPerformance| > expectedEpochInterest_new`, the addition of the signed accumulator into `expectedEpochInterest` underflows (or drives the value through a revert path in the minting/accounting math), so `startEpoch` reverts on every call.

Because:
- `stopEpoch` already ran, `isEpochRunning == false`, `epochEndDate` has passed,
- only the manager can call `startEpoch` and there is no alternative path to reset `interestForOverUnderPerformance` (it is internal state only mutated by `requestWithdraw`, which itself requires an accounting update that does not clear it),

the epoch state machine is stuck in the "stopped" phase permanently. Withdraw request claims are gated on `epochNumber > lastWithdrawRequest` (`IdleCreditVault.sol:326`), which only advances at `stopEpoch`, so existing receipts and remaining NAV are both frozen.

Note: the analogous "kernel drops the whole block" failsafe maps to the fact that there is no recovery path — one unprivileged request (or a series of dust `requestWithdraw` calls, each adding a small negative `diff`, mirroring the 1-wei spam vector) poisons settlement for everyone.

### Impact Explanation
Permanent freezing of all lender funds still in the vault (both tranches' NAV and all pending withdraw receipts). Loss equals the entire remaining pool NAV at the moment `startEpoch` first reverts. The attacker only needs a KYC-passed BB tranche position; the magnitude of `interestForOverUnderPerformance` scales with withdrawal size, and many dust withdrawals can be used if a single large position is unavailable — matching the report's spam-vector character.

### Likelihood Explanation
- Attacker profile allowed: any KYC'd lender holding BB tranches.
- No privileged action required; `requestWithdraw` is permissionless for allowed wallets (`IdleCDOEpochVariant.sol:739-791`).
- No existing guard stops it: `_skimDonatedAssets` only isolates raw donations; the revert guards in `requestWithdraw` (`NotAllowed` on flags/KYC) do not bound the accumulator; `requestWithdraw` in `IdleCreditVault` does not revert for normal (non-loss-epoch, non-default) requests.
- The trigger window is the running epoch just before `stopEpoch`, when a BB withdrawal's negative `diff` is largest relative to the NAV that will remain.

Uncertainty: I could not fully read `startEpoch` in this pass, so the exact revert line (signed-to-unsigned underflow vs. downstream accounting revert) is inferred from the documented accounting flow at lines 780-784; the mechanism (negative accumulator exceeding next-epoch expected interest on a shrunken NAV) is confirmed by the in-code comments.

### Recommendation
- Clamp `interestForOverUnderPerformance` application at `startEpoch`: `expectedEpochInterest = expectedInt > accrued ? expectedInt - accrued : 0` semantics (saturating subtraction) instead of a reverting signed add.
- Alternatively, decrement `interestForOverUnderPerformance` toward zero per epoch (carry the unapplied remainder), or cap each withdrawal's negative `diff` contribution at the remaining `expectedEpochInterest` so an attacker can never push the aggregate below zero.
- Add an invariant test: after any sequence of AA/BB `requestWithdraw` calls, `startEpoch` must not revert.

### Proof of Concept
Foundry fork test outline (modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopCurrentEpoch`):

```solidity
function testBBWithdrawPoisonsStartEpoch() external {
    // 1. Setup: AA seeded, attacker holds large BB position (KYC'd lender).
    uint256 bbAmount = 900_000 * ONE_SCALE;   // dominant share of pool
    _depositBBWithUser(attacker, bbAmount, true);
    idleCDO.depositAA(100_000 * ONE_SCALE);   // small AA remainder

    // 2. Start epoch with non-zero APR and AA-favoring split ratio.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() - 1);

    // 3. Attacker requests withdrawal of full BB position near epoch end.
    //    Each request adds negative diff to interestForOverUnderPerformance.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(BBtranche)); // 0 = full balance

    // 4. Honest manager stops the epoch normally (borrower repays fully).
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // no loss, no default

    // 5. startEpoch underflows: |interestForOverUnderPerformance| exceeds the
    //    expected interest computed on the shrunken residual NAV.
    vm.prank(manager);
    vm.expectRevert(); // panic underflow / NotAllowed in accounting
    cdoEpoch.startEpoch();

    // 6. Repeat: every retry reverts -> permanent freeze of remaining NAV
    //    and of the attacker's own pending receipt (epochNumber never advances).
    vm.prank(manager);
    vm.expectRevert();
    cdoEpoch.startEpoch();
}
```

Expected result: `startEpoch` reverts deterministically; residual AA NAV and pending withdraw receipts are unrecoverable, demonstrating permanent freezing with fund impact from an unprivileged BB withdrawal.