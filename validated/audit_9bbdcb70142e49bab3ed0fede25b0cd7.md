### Title
ProgrammableBorrower withdraw-liquidity reserve is sized once at `onStartEpoch` and never grown when mid-epoch `requestWithdraw` increases `pendingWithdraws`, so a fully-drawn facility is forced into default and pending receipts/LPs are haircut - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower.onStartEpoch(_pendingWithdraws)` snapshots the withdraw-request liability into `epochPendingWithdraws`, which is the only amount `availableToBorrow()` excludes from borrowable liquidity. If new withdraw requests arrive while the epoch is running, `IdleCreditVault.pendingWithdraws` grows but the borrower's reserve does not. `borrow()` can therefore lend out funds that `IdleCDOEpochVariant.stopEpoch` will later demand via `onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws)`, causing the pull to fail and the CDO to take the `_handleBorrowerDefault` path even though the borrower is solvent. This mirrors the kernel bug: a buffer sized for the initial page size is reused after a larger on-disk page size is adopted, without resizing.

### Finding Description
- At epoch start the reserve is fixed once: `epochPendingWithdraws = _pendingWithdraws` (`ProgrammableBorrower.sol:205`).
- Borrow capacity is `totalAssets - epochPendingWithdraws` (`ProgrammableBorrower.sol:350-355`), so only the *stale, start-of-epoch* liability is protected.
- `IdleCreditVault.requestWithdraw` happily increases `pendingWithdraws` during a running epoch (it only checks `epochEndDate == 0` for closed pools; `IdleCreditVault.sol:259,279`).
- At `stopEpoch`, `IdleCDOEpochVariant` re-reads the *grown* `pendingWithdraws` after `prepareStopEpochWithApr0` (`IdleCDOEpochVariant.sol:363-364`) and requests `expectedInterest + pendingWithdraws` from the borrower via `onStopEpoch`/`getFundsFromBorrower` (`IdleCDOEpochVariant.sol:398,408`).
- In `onStopEpoch`, when the cash shortfall exceeds `_currentVaultAssets()` the hook returns `true` and lets the subsequent `transferFrom` fail (`ProgrammableBorrower.sol:239-245`), triggering `_handleBorrowerDefault` (`IdleCDOEpochVariant.sol:401`).

The broken invariant: the reserve that is supposed to guarantee epoch-end withdraw liquidity is not resized when the liability it covers grows — exactly the "allocate at size X, adopt size Y>X, use size Y" pattern of CVE-2026-72470.

### Impact Explanation
A tranche holder who `requestWithdraw`s mid-epoch on a nearly-fully-drawn programmable facility forces a spurious default at `stopEpoch`: `transferFrom` reverts, `_handleBorrowerDefault` runs, and pending receipts plus active LPs are haircut through `previewLossAdjustedWithdrawFunds`/`finalizeDefaultRecovery` even though no real credit loss occurred. All withdrawals are frozen until the facility is unwound through the default/recovery flow (temporary freezing of all funds, with pro-rata receipt haircuts and BB-first active loss if the recovered amount is below basis). Quantified loss: every withdraw requested after epoch start, up to the amount the borrower has drawn beyond the stale reserve, becomes unfunded at stop.

### Likelihood Explanation
Medium. Requires (a) a programmable-borrower pool where the borrower has drawn most of `availableToBorrow()` (the facility's normal purpose), and (b) any tranche holder calling `requestWithdraw` during the running epoch — a routine user action, not privileged. Honest keeper/borrower behavior is sufficient; no role misbehaves. The reserve-update path simply does not exist (no setter or hook refreshes `epochPendingWithdraws` mid-epoch).

### Recommendation
Keep the borrow reserve synchronized with the live liability: either have `IdleCDOEpochVariant.requestWithdraw` (or `IdleCreditVault.requestWithdraw`) notify the programmable borrower to increase `epochPendingWithdraws` by the new request amount, or have `availableToBorrow()` read `IIdleCDO(idleCDO)`'s current `pendingWithdraws` instead of the epoch-start snapshot. The latter is simplest: replace `reserved = epochPendingWithdraws` with the live strategy value so borrows can never consume liquidity that newer receipts will need at `stopEpoch`.

### Proof of Concept
Foundry fork PoC (sketch, using the existing `ProgrammableBorrowerAccountingInvariant` harness):

```solidity
function testMidEpochWithdrawRequestForcesDefault() external {
    uint256 startAssets = 100_000e18;
    _startEpoch(startAssets);                       // onStartEpoch(0): epochPendingWithdraws = 0
    // borrower draws everything (honest, keeper-triggered)
    vm.prank(realBorrower);
    borrowerContract.borrow(0);                     // borrows ~full startAssets, reserve = 0

    // attacker: tranche holder requests withdraw while epoch is running
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // IdleCreditVault.pendingWithdraws += amount

    // honest manager stops epoch
    deal(defaultUnderlying, borrower, 0);           // borrower cannot instantly repay (drawn)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);      // getFundsFromBorrower transferFrom fails
    assertTrue(cdoEpoch.defaulted());               // spurious default -> receipts + LPs haircut
}
```

Key assertion: `IdleCreditVault.pendingWithdraws()` at stop is strictly greater than the `epochPendingWithdraws` the borrower reserved, so the pull fails and `defaulted()` becomes true despite a solvent borrower.