### Title
Accrued `unclaimedFees` become permanently stranded once the pool is closed or defaulted — NAV stays reduced while the fees can never be paid out - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The closest analog to "protocol fees minted into a module that can never be withdrawn" is the carried `unclaimedFees` balance in the epoch variant. In cash mode, `stopEpochWithDuration` pays fees only up to `_grossInterest - _pendingWithdrawFees`; any excess is left in `unclaimedFees`, which **continues reducing NAV** via `getContractValue` (NAV is reported net of `unclaimedFees`). The only mechanism that ever disburses `unclaimedFees` is that same stop-epoch fee block — it depends on new borrower cash arriving each epoch. When the pool is closed (`stopEpochWithDuration` with `_isRequestingAllFunds`, which sets `epochDuration = 0` / `epochEndDate = 0` and `disableInstantWithdraw = true`) or when the borrower defaults (`_handleBorrowerDefault`), no future epoch can run, so the carried fees are never paid — yet they keep depressing tranche prices/NAV. The underlying backing those fees stays trapped in the CDO contract with no disbursement path (the only exits are user withdrawals at the fee-reduced price and donation skimming, which does not route it to the fee recipients as accounted fees). This mirrors the ZetaChain bug: an amount is earmarked for the protocol in accounting, the earmark persists and affects state, but no execution path ever delivers it.

### Finding Description
Relevant code:

- `IdleCDOCreditVault._accrueManagementFee` adds management fees to `unclaimedFees` every accounting tick, and `_updateAccounting` adds performance fees (`unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC`), while `getContractValue` reports NAV **minus** `unclaimedFees` — so the accrual immediately reduces what tranche holders can redeem.
- `IdleCDOEpochVariant.stopEpochWithDuration` (≈L436-459): in non-minted mode it computes `_availableForFees = _grossInterest - _pendingWithdrawFees`, clamps the payout, calls `_transferFeeUnderlyings(_fees)`, then does `unclaimedFees -= _fees` — leaving the remainder accrued. The comment states: *"Any fee that cannot be paid in cash remains accrued and continues reducing NAV."*
- In the close-pool branch (≈L488-493) `epochDuration = 0; epochEndDate = 0; disableInstantWithdraw = true` — the vault enters terminal mode; `stopEpoch`/`startEpoch` can never be called again, so the fee-disbursement block is unreachable for the carried balance.
- On the default path (`_handleBorrowerDefault`), no fee settlement occurs; carried `unclaimedFees` similarly survive while post-default pricing and recovery distribution keep treating that slice of underlying as owed to fee recipients who can never claim it.

Broken invariant: fair NAV accounting / one-receipt-one-payout. Tranche holders permanently absorb a NAV haircut equal to the carried `unclaimedFees`, while the corresponding underlying is neither paid to `feeReceiver`/`owner` nor released back to lenders — it is stranded in the contract (and can only ever leave via the donation/skim path, which is not fee settlement).

### Impact Explanation
Permanent freezing of funds / stuck protocol fees. If a vault closes (or the borrower defaults) while `unclaimedFees > 0` — e.g., a long epoch where management fee accrual exceeded gross interest (the repo's own test `testStopEpochCarriesManagementFeesAboveGrossInterest` shows such carried balances are a designed case) — then for the life of the closed vault: tranche prices and `maxWithdrawable` remain reduced by `unclaimedFees`, lenders recover less underlying than the true residual balance, and the difference (equal to the carried fees) is locked in the IdleCDO with no retrieval function. Quantified loss = the carried `unclaimedFees` at close/default time, borne effectively by lenders (lower payout) or, if skimmed, by fee recipients (never paid). No attacker or privileged misbehavior is required — it is a pure logic/accounting flaw, same as the source report.

### Likelihood Explanation
Requires a nontrivial carried fee balance at the moment of close or default: management fee accrual exceeding cash interest, or a stop with low `_grossInterest`. Management fee is capped (`MAX_FEE / 10`), and managers choosing low/zero interest epochs or closing a pool after a weak epoch are plausible operational states; defaults are an anticipated path with dedicated handling. Likelihood is moderate — it needs the combination of carried fees and terminal vault state, but neither precondition needs an adversary.

### Recommendation
On terminal transitions, reconcile `unclaimedFees` instead of leaving it accrued:
- In the close-pool branch and in `_handleBorrowerDefault`/`finalizeDefault`, either (a) pay carried `unclaimedFees` in cash from the recalled principal to `feeReceiver`/`owner` (or mint AA shares as in minted mode) before computing user-claimable NAV, or (b) explicitly zero `unclaimedFees` so NAV is not reduced by fees that can never be collected, letting the residual underlying flow back to lenders.
- Mirror the fix pattern of the source report: add an explicit retrieval/settlement path for accrued fees rather than letting them accumulate in the contract.

### Proof of Concept
Foundry fork PoC outline (test-only code, to be placed under `test/foundry/`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IdleCreditVault.t.sol"; // reuse existing harness/helpers

contract CarriedFeesStrandedPoC is IdleCreditVaultTest {
    function test_carriedUnclaimedFeesStrandedAfterClose() external {
        uint256 amount = 10_000 * ONE_SCALE;
        uint256 mgmtFeeRate = 2_000; // 2% annualized

        // feeReceiver = TL_MULTISIG, all fees to feeReceiver, mgmtFee = 2%
        _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
        _setManagementFee(mgmtFeeRate);

        idleCDO.depositAA(amount);
        _transferBurnedTrancheTokens(address(this), true);

        vm.startPrank(manager);
        cdoEpoch.setEpochParams(365 days, 0);
        IdleCreditVault(address(strategy)).setApr(initialProvidedApr);
        cdoEpoch.startEpoch();
        vm.stopPrank();

        // borrower repays only a tiny amount of interest -> fees exceed gross interest
        uint256 lowInterest = 100 * ONE_SCALE;
        deal(defaultUnderlying, borrower, lowInterest);
        vm.warp(cdoEpoch.epochEndDate());
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, lowInterest);

        uint256 carried = cdoEpoch.unclaimedFees();
        assertGt(carried, 0, "fees carried");

        // Close the pool: request all funds, epochDuration/epochEndDate -> 0
        deal(defaultUnderlying, borrower, amount);
        vm.prank(manager);
        cdoEpoch.stopEpochWithDuration(0, 0, amount); // _isRequestingAllFunds path

        // Terminal state: no future epoch can ever settle `carried`
        assertEq(cdoEpoch.epochEndDate(), 0, "pool closed");
        assertGt(cdoEpoch.unclaimedFees(), 0, "fees still accrued, never payable");

        // NAV stays reduced by the stranded fee; underlying backing it is locked
        // getContractValue() = realBalance - unclaimedFees, while no call path
        // can transfer `unclaimedFees` to feeReceiver/owner anymore.
    }
}
```

Key assertion: `unclaimedFees > 0` after pool closure, with `epochEndDate == 0` guaranteeing no further `stopEpochWithDuration` fee-settlement branch can ever execute — the fee slice of NAV is permanently stranded, the exact analog of protocol fees accumulating unclaimably in the `crosschain` module. Note: I could not fully inspect `_skimDonatedAssets`/default-finalization internals within the available iterations; if skim sweeps the stranded balance to fee recipients, the loss shifts from lenders to fee accounting but the "accrued yet never properly settled" flaw stands — worth confirming during PoC execution.