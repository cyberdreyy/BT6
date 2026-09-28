### Title
Missing accrued management-fee sync before epoch interest pricing leads to overstated `expectedEpochInterest` and forced borrower default - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`startEpoch()` computes the borrower's epoch obligation as `_calcInterest(getContractValue())`, but `getContractValue()` uses the checkpointed `lastNAVAA + lastNAVBB` minus the stored `unclaimedFees`. Unlike `requestWithdraw()` and `depositDuringEpoch()`, which both call `_updateAccounting()` (which accrues management fees via `_accrueManagementFee()`) before pricing, `startEpoch()` only calls `_skimDonatedAssets()`. Management fees accrued over the `bufferPeriod` since the last accounting checkpoint are never deducted, so NAV — and therefore the interest demand placed on the borrower — is overstated. This is the direct analog of the external report: a cached, stale aggregate (here fee-adjusted NAV; there `reserve.totalUsage`) is consumed by an interest calculation after the accrual clock has moved forward.

### Finding Description
The calculation flow is:

1. `stopEpoch()` at the previous epoch end calls `_updateAccounting()`, which checkpoints `unclaimedFees` and sets `latestHarvestBlock` (IdleCDOEpochVariant.sol:436).
2. The `bufferPeriod` elapses. Management fees keep accruing implicitly, but `unclaimedFees` is a stored variable and stays stale.
3. Manager calls `startEpoch()`. At line 260, `expectedEpochInterest` is computed:
   ```solidity
   int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
   ```
   `getContractValue()` subtracts the *stale* `unclaimedFees`, overstating the active NAV by exactly the management fee accrued during the buffer period (`_calculateManagementFee(NAV, block.timestamp - latestHarvestBlock)`).
4. The same stale NAV inflates `_toSend`/pricing and, critically, `expectedEpochInterest`, which becomes the hard cash demand enforced at `stopEpoch` via `getFundsFromBorrower` (IdleCDOEpochVariant.sol:408). In minted mode it inflates `mintStrategyTokens(_grossInterest)` and `borrowerInterestDebt` via `settleBorrowerInterest()` (ProgrammableBorrower.sol:275-283).

By contrast, `requestWithdraw()` calls `_updateAccounting()` at line 750 before `_calcInterestWithdrawRequest`, and `depositDuringEpoch()` calls it at line 682 — confirming the intended invariant that all interest math runs on freshly accrued NAV. `startEpoch()` is the only pricing entry point that skips it.

### Impact Explanation
High. The overstatement equals `mgmtFeeAccruedOverBuffer * (apr/100) * epochDuration / 365 days`, which is a phantom receivable with no real yield behind it:

- If the borrower repays anyway (or `isInterestMinted` mints it), tranche prices are inflated by phantom yield — LPs who then redeem extract real backing that belongs to other LPs, and `borrowerInterestDebt` grows by an uncollectable amount that later materializes as a real default.
- If the honest borrower's `transferFrom` is short by the phantom amount, the `try/catch` at line 501 falls into `_handleBorrowerDefault()`, which pauses the vault, blocks all withdraw requests, and pushes active LPs and pending receipts into the loss-socialization waterfall of `finalizeDefault` — a permanent loss caused purely by a bookkeeping mismatch, not by any borrower insolvency.

### Likelihood Explanation
High. Any deployment with `managementFee > 0` is affected on every epoch cycle, proportional to `bufferPeriod`. `bufferPeriod` defaults to 5 days (line 73), so the overstatement accrues automatically each epoch without any attacker action beyond being a tranche holder benefiting from (or suffering the default caused by) the inflated interest.

### Recommendation
Checkpoint accrued management fees (and any other time-accrued NAV adjustments) before computing `expectedEpochInterest` in `startEpoch()`:

```solidity
_skimDonatedAssets();
_accrueManagementFee();        // sync unclaimedFees with elapsed time
_updateAccounting();           // or at minimum refresh lastNAV checkpoints
int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
```

Apply the same ordering to `writeOffDeposit()` (lines 936-962), which also runs `_calcInterestWithdrawRequest` on stale NAV without calling `_updateAccounting()`.

### Proof of Concept
Foundry fork sketch (real underlying + deployed `IdleCDOEpochVariant` with `managementFee > 0`):

```solidity
// 1. Deposit AA, run one epoch, manager calls stopEpoch(0,0)
idleCDO.depositAA(amount);
vm.prank(manager); cdoEpoch.startEpoch();
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager); cdoEpoch.stopEpoch(newApr, 0);   // checkpoints unclaimedFees

// 2. Wait the full bufferPeriod so management fees accrue
vm.warp(block.timestamp + cdoEpoch.bufferPeriod() + 1);
uint256 accruedFee = navAA_BB * managementFee * elapsed / (365 days * FULL_ALLOC); // expected stale amount

// 3. Manager starts the next epoch; expectedEpochInterest is priced on stale NAV
vm.prank(manager); cdoEpoch.startEpoch();
assertApproxEqAbs(
    cdoEpoch.expectedEpochInterest(),
    _calcInterest(getContractValue_with_fees_stale),
    ...
);
// Inflation ≈ accruedFee * scaledApr/100 * epochDuration/365d

// 4a. Cash mode: borrower approved only the true obligation ->
// stopEpoch transferFrom fails -> _handleBorrowerDefault -> defaulted == true,
// withdraw requests frozen, LPs enter finalizeDefault loss waterfall.
// 4b. Minted mode: settleBorrowerInterest() inflates borrowerInterestDebt by the
// phantom amount; borrower.repay() cannot cover it; later close-pool onStopEpoch
// returns false (borrowerInterestDebt != 0) and the pool defaults.
```

Note: I could not fully verify the internal body of `_updateAccounting()`/`_accrueManagementFee()`/`getContractValue()` in `IdleCDOCreditVault.sol` within the available context — the claim rests on the inline comment at lines 382-384 ("Checkpoint management fees before borrower funds are pulled") and the explicit `_updateAccounting()` calls in `requestWithdraw`/`depositDuringEpoch` establishing the intended sync invariant. A PoC must confirm `getContractValue()` deducts stored `unclaimedFees` rather than computing fees live; if it computes them live, no vulnerability exists.