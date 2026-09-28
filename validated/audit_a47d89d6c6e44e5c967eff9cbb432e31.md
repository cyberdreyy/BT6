### Title
Mid-epoch withdraw requests are not reserved in `ProgrammableBorrower`, so honest borrows can force a spurious default that haircuts pending receipt holders - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The external bug class is "a fund-draining path that ignores collateral already reserved for unclaimed claims." In idle-tranches the closest surface is the programmable-borrower facility: `availableToBorrow()` reserves only `epochPendingWithdraws`, which is a snapshot taken once in `onStartEpoch()` and is never increased when new withdraw requests are created mid-epoch. A borrower draw executed after a mid-epoch `requestWithdraw` can therefore strip liquidity that `stopEpoch` must pull to fund the pending receipts, causing `getFundsFromBorrower` to fail and routing the pool into the default/recovery path where receipt claims are haircut.

### Finding Description
`onStartEpoch(uint256 _pendingWithdraws)` snapshots the reserved amount: `epochPendingWithdraws = _pendingWithdraws` at `contracts/strategies/idle/ProgrammableBorrower.sol:205`. `availableToBorrow()` then caps draws at `totalAssets - epochPendingWithdraws` (`ProgrammableBorrower.sol:350-355`), and `_borrow` enforces `assets > borrowable → revert InsufficientBorrowable` (`ProgrammableBorrower.sol:445`).

The problem: `epochPendingWithdraws` is only written in `onStartEpoch`, `onStopEpoch` (`:264`) and `onDefault` (`:298`). There is no hook that increases it when new requests arrive.

When a user calls `requestWithdraw` mid-epoch, `IdleCreditVault.requestWithdraw` only burns/mints strategy tokens and does `pendingWithdraws += _amount` (`contracts/strategies/idle/IdleCreditVault.sol:272-280`). It makes no call into `ProgrammableBorrower`, so the borrower's reserved bucket stays at the stale epoch-start value.

At `stopEpoch`, `IdleCDOEpochVariant` reads the *live* `pendingWithdraws` and pulls `_amountToPullFromBorrower + _pendingWithdraws` from the borrower via `this.getFundsFromBorrower(...)` (`contracts/IdleCDOEpochVariant.sol:398-410`). In `onStopEpoch`, the programmable borrower tries to free liquidity from the vault, but if the shortfall exceeds vault assets because the borrower already drew the unreserved cash, it returns `true` with insufficient on-hand balance (`ProgrammableBorrower.sol:239-245`), the subsequent `transferFrom` fails, and IdleCDO treats it as a borrower default. `collectWithdrawFunds` then either stores a haircut `lossRecoveryPriceByEpoch` or the receipts are settled through `finalizeDefaultRecovery` at `recoveryPrice < RECOVERY_FULL`.

### Impact Explanation
Pending withdraw receipts created mid-epoch are not backed by a borrow-time reservation, breaking the solvency invariant "pending receipts are always funded at stopEpoch." Concretely: an LP requesting withdrawal during an active epoch has their claim (a) haircut via `lossRecoveryPriceByEpoch` or the default recovery price, or (b) frozen until the borrower repays enough to fund `defaultRecoveryReserve`. The quantified loss equals `pendingWithdraws - min(pendingWithdraws, fundedAmount)` — up to the full request amount when the borrower has drawn all non-snapshot liquidity — plus the haircut imposed on co-existing active tranche holders through the forced default waterfall.

### Likelihood Explanation
Requires only ordinary sequencing: an unprivileged KYC-passed lender calls `requestWithdraw` during a running epoch, and the (honest, per-threat-model) borrower performs a routine `borrow`/`executeBorrow` of the now-unreserved balance before `stopEpoch`. No malicious privileged role is needed — the borrower profits are irrelevant; the trigger is a normal draw that the stale `epochPendingWithdraws` snapshot fails to block. No existing guard covers this: `availableToBorrow` sees the old reserve, `onStopEpoch` cannot mint liquidity, and the default path deliberately redistributes the shortfall onto receipt holders.

### Recommendation
Keep the borrow-side reservation synchronized with live pending withdrawals. Either have `IdleCreditVault.requestWithdraw` (or the CDO on its behalf) call a `_checkOnlyIdleCDO`-gated `increasePendingWithdraws(uint256)` on `ProgrammableBorrower` that does `epochPendingWithdraws += _amount`, or make `availableToBorrow()` read the live `IIdleCreditVault(strategy).pendingWithdraws()` delta added since `onStartEpoch`. Symmetrically decrease the reserve if requests are ever cancelable.

### Proof of Concept
Foundry fork test outline (programmable mode, minted or cash interest, running epoch):

```solidity
// Setup: pool in running epoch; ProgrammableBorrower holds assets A (idle + vault),
// epochPendingWithdraws == snapshot from onStartEpoch (assume 0 for max clarity).

// 1. Victim (any lender) requests withdraw of W during the active epoch.
vm.prank(victim);
cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));
// strategy.pendingWithdraws() == W, but borrowerContract.epochPendingWithdraws() still == 0

// 2. Honest borrower draws up to availableToBorrow() — nearly all of A.
vm.prank(borrower);
borrowerContract.borrow(0); // draws full free amount; W is not reserved

// 3. Manager stops the epoch.
vm.prank(manager);
cdoEpoch.stopEpoch(); // getFundsFromBorrower(interest + W) -> transferFrom fails -> _handleBorrowerDefault

// Assertions:
assertTrue(cdoEpoch.defaulted());
// 4. Victim claims after finalization with a haircut or waits on recovery.
// strategy.lossRecoveryPriceByEpoch(epoch) < RECOVERY_FULL  (or)
// claim pays W * defaultRecoveryPrice / RECOVERY_FULL < W
```

The key reproducible step is that `availableToBorrow()` returns `> A - pendingWithdraws` after step 1, proving the reservation is stale; the remaining steps follow the existing default-handling code path unchanged.