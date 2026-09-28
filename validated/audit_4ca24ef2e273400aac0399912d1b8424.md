### Title
Stale `borrowerInterestAccrued` is never cleared on cash-funded (non-minted) stops, double-counting borrower interest and blocking pool closure - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
`IdleCDOEpochVariant._stopEpoch` only calls `ProgrammableBorrower.settleBorrowerInterest()` when interest is minted (`_mintInterest && isProgrammableBorrower`). In the cash-funded path the CDO pulls the accrued borrower interest as underlying via `getFundsFromBorrower`, but `ProgrammableBorrower` never clears `borrowerInterestAccrued`. The same interest is then counted again in `totalInterestDueNow()` at the next stop, and `borrowerInterestAccrued != 0` permanently blocks the close-pool path in `onStopEpoch`.

### Finding Description
- `ProgrammableBorrower.borrowerInterestAccrued` is reduced only in `_repay` (lines 505-521) and `settleBorrowerInterest` (lines 275-283). `settleBorrowerInterest` is invoked from `IdleCDOEpochVariant.sol:413-415` exclusively under the minted-interest branch.
- In non-minted mode, `stopEpoch` resolves `_interest` via `totalInterestDueNow()` (line 1004), which includes `borrowerInterestAccruedNow()` (line 332), and pulls that amount in cash through `this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws)` (line 408). The PB ledger keeps the stale accrued balance.
- Additionally, the cash pulled comes from the facility's vault liquidity (lender principal), not from the real borrower — the real borrower still owes the same `borrowerInterestAccrued`/`borrowerInterestDebt`, which it will pay again through `repay()`/`_repay` via `safeTransferFrom(borrower, ...)` (line 482). That repayment is deposited to the vault with only the non-interest portion extending the principal baseline (line 527), so it is counted yet again as `vaultInterest`.
- `onStopEpoch` line 235 returns `false` whenever `borrowerInterestAccrued != 0`, so `_isRequestingAllFunds` always falls into `_handleBorrowerDefault` (line 401) even when the borrower fully repaid — an honest borrower is defaulted and the pool can never close cleanly.

### Impact Explanation
Two compounding effects, both quantified by the first epoch's accrued borrower interest `X` (and repeated each subsequent epoch):

1. **Inflated yield paid from principal**: at every later `stopEpoch`, `totalInterestDueNow()` still includes the stale `X`, so the CDO pulls or mints `X` of phantom interest sourced from PB vault assets (lender principal). `_updateAccounting` distributes it per `trancheAPRSplitRatio`, so AA holders mint/redeem value that is backed by BB/principal funds — a direct wealth transfer to whoever holds tranche tokens.
2. **Permanent close-pool DoS / forced default**: close-pool (`_interest == 1`) always returns `false` from `onStopEpoch`, routing to `_handleBorrowerDefault`, which pauses deposits, blocks withdraw requests, and funnels all claims through the default/recovery waterfall — lenders absorb losses despite the borrower being solvent.

### Likelihood Explanation
Requires a programmable-borrower deployment running with `isInterestMinted == false` (cash-funded interest) and non-zero `borrowerApr` with at least one borrow during an epoch — then the stale accrual arises automatically on the first `stopEpoch`, no privileged misbehavior needed. An attacker needs only be a KYC-passing lender holding AA tranche tokens before an epoch boundary to harvest the inflated distribution, or any user to benefit from/trigger the forced default on close. If the deployment intends minted mode only, the bug is latent but unguarded — nothing prevents `setIsInterestMinted(false)` or a non-minted configuration.

### Recommendation
Clear borrower-side accrual consistently for both funding modes: either call `settleBorrowerInterest()` (or a new `clearBorrowerInterest()`) unconditionally on the successful `stopEpoch` path in `IdleCDOEpochVariant.sol:408-415`, or enforce minted-only mode for programmable borrowers (revert in `stopEpoch`/setter when `isProgrammableBorrower && !isInterestMinted`). Add a regression test that a non-minted stop leaves `borrowerInterestAccrued == 0` and that close-pool succeeds after full repayment.

### Proof of Concept
Foundry fork test sketch (modeled on `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testStaleAccruedInterestDoubleCount() external {
    // non-minted programmable mode
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(false);

    uint256 amount = 100_000 * oneScale;
    idleCDO.depositAA(amount);            // attacker KYC'd AA lender
    _startEpochAndCheckPrices(0);

    // real borrower draws and accrues contractual interest
    vm.prank(revolvingBorrower);
    programmableBorrower.borrow(50_000 * oneScale);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 accruedBefore = programmableBorrower.borrowerInterestAccruedNow();
    assertGt(accruedBefore, 0);

    // manager stops epoch: interest pulled in cash, but PB ledger not cleared
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // BUG 1: accrued interest still outstanding although already paid to the pool
    assertEq(programmableBorrower.borrowerInterestAccrued(), accruedBefore);

    // borrower repays; next epoch runs; stale accrual counted again in totalInterestDueNow
    // -> CDO pulls/mints accruedBefore a second time, sourced from vault principal,
    //    inflating AA price at BB/principal expense.

    // BUG 2: close-pool can never succeed -> forced default on honest borrower
    // after borrower fully repays via repay(0), accruedBefore was still outstanding
    // pre-repay; even if repaid, verify the stale-double-count already extracted value.
    // Direct check: closing with _interest == 1 while accrued != 0 reverts to default:
    //   IProgrammableBorrower.onStopEpoch returns false -> _handleBorrowerDefault
}
```

Key assertions: after a successful non-minted `stopEpoch`, `borrowerInterestAccrued` remains non-zero (`ProgrammableBorrower.sol:275-283` never invoked because the call site at `IdleCDOEpochVariant.sol:413` is gated on `_mintInterest`); `totalInterestDueNow()` at the next stop re-includes the stale amount; and `onStopEpoch` line 235 makes `_isRequestingAllFunds` return `false` whenever any stale accrual persists.

Uncertainty noted: I could not read `IdleCDOEpochVariant.sol` lines ~300-380 (`_amountToPullFromBorrower` composition) to confirm the exact pull amount in non-minted mode, and whether production deployments always set `isInterestMinted = true`. If the cash pull excludes `borrowerInterestAccrued`, the double-count severity reduces to the close-pool-blocking issue; either way the missing settlement call is a concrete defect.