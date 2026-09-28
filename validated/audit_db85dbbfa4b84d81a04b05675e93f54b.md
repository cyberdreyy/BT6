### Title
Non-repaying borrower accrues unbacked minted interest indefinitely — default check ignores outstanding `borrowerInterestDebt`/`borrowerInterestAccrued` - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external Lavarage bug class (liquidation check ignores accrued interest owed, so a borrower who never repays is never liquidated) maps cleanly onto the minted-interest mode of `IdleCDOEpochVariant` + `ProgrammableBorrower`. In minted mode (`isInterestMinted == true`), `stopEpoch` pulls **zero** interest cash from the borrower (`_amountToPullFromBorrower = 0` at `contracts/IdleCDOEpochVariant.sol:376`), settles accrued interest into `borrowerInterestDebt` via `settleBorrowerInterest()`, and mints that yield into tranche prices — but no code path ever checks whether the borrower actually paid previously fronted interest. `onStopEpoch` only fails (i.e. triggers `_handleBorrowerDefault`) when `_isRequestingAllFunds` is true and debt exists (`contracts/strategies/idle/ProgrammableBorrower.sol:235`), or when a `transferFrom` for `_amountToPullFromBorrower + _pendingWithdraws` fails (`contracts/IdleCDOEpochVariant.sol:408,504`). With `pendingWithdraws == 0`, both amounts are zero/none, so a borrower who has never paid a single wei of interest still passes the solvency gate every epoch.

### Finding Description
1. Owner sets `isInterestMinted(true)`; lenders deposit; manager `startEpoch`.
2. Borrower draws principal via `borrow()` — `availableToBorrow()` (`ProgrammableBorrower.sol:350`) subtracts only `epochPendingWithdraws`, never `borrowerInterestDebt + borrowerInterestAccrued`.
3. At `stopEpoch`, `totalInterestDueNow()` reports contractual borrower interest; the CDO mints it into NAV (`deposit(0)` / minted path, `IdleCDOEpochVariant.sol:376-466`) and `settleBorrowerInterest()` moves it into `borrowerInterestDebt` (`ProgrammableBorrower.sol:275-283`). No cash changes hands.
4. `repay()` orders repayments interest-debt-first (`ProgrammableBorrower.sol:487-521`), but nothing compels repayment. The epoch loops forever with `transferFrom(0)` succeeding, `skipDefaultCheck` untouched, `_unpause()` re-enabling requests (`IdleCDOEpochVariant.sol:478-486`).
5. Tranche `virtualPrice` keeps rising on minted, never-collected yield. Early withdrawers redeem at inflated prices against the real vault cash sleeve, leaving late holders with claims backed only by the borrower's unpaid `borrowerInterestDebt`. The facility only recognizes the default when the manager calls `stopEpoch(0,1)` (close-pool), at which point `onStopEpoch` returns false (`ProgrammableBorrower.sol:235`) and the loss is socialized BB-first — exactly the "indefinite lockup / bad debt only in the books" outcome the Appellate Court ruled High.

### Impact Explanation
- Minted yield is paid out to redeeming lenders from real liquidity while the corresponding borrower receivable is never enforced; the pool accrues growing bad debt (`borrowerInterestDebt` unbounded). This is a direct loss of funds socialized onto remaining AA/BB holders — the same "lender lends for free, cannot force liquidation" harm as Lavarage H-03, upgraded to High on the same reasoning (indefinite lockup + bad debt).

### Likelihood Explanation
- Requires only the configured borrower to simply not repay — including the non-adversarial lost-keys/abandoned-loan scenario explicitly accepted in the Lavarage ruling. No privileged misbehavior needed; manager honesty doesn't help because the honest close merely crystallizes the already-accrued loss.

### Recommendation
Include outstanding borrower obligations in the stop-epoch solvency check: have `onStopEpoch` (or `_stopEpoch`) return failure when `borrowerInterestDebt`/`borrowerInterestAccrued` exceed a threshold (e.g., a fraction of facility NAV), or cap total `borrowerInterestDebt` accrual and force default/close once the borrower's effective LTV breaches a bound. Alternatively, require periodic real repayment of `borrowerInterestDebt` before allowing new borrows in `_borrow`.

### Proof of Concept
Foundry fork sketch (mirrors `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):
```solidity
// setup: setIsInterestMinted(true); depositAA(10_000e6); startEpoch
vm.prank(revolvingBorrower);
programmableBorrower.borrow(drawAmount);          // borrower draws
for (uint i; i < N; ++i) {
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);                     // succeeds every epoch
    // borrowerInterestDebt grows, virtualPrice grows, defaulted == false
}
// borrower never calls repay(); AA withdrawer redeems at inflated price;
// remaining holders' NAV is backed by unpaid borrowerInterestDebt only.
```
Assertions: `cdoEpoch.defaulted() == false` throughout; `programmableBorrower.borrowerInterestDebt()` strictly increasing; `aaTranche` redeemers receive cash exceeding real assets, proving unbacked yield payout.