### Title
`repay()` re-deposits repaid funds into the ERC4626 vault unconditionally — a vault deposit failure reverts the entire repayment and forces a solvent borrower into default - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
The external report describes a function that repays debt first and then unconditionally converts the remainder into collateral, so a failure of the second step reverts the whole transaction and leaves the user's debt outstanding (eventually liquidated/defaulted). The exact same structure exists in `ProgrammableBorrower._repay`: it clears the borrower's tracked debt buckets, then calls `_depositToVault` → `vault.deposit(...)` with no `try/catch` and no `maxDeposit` pre-check. If the ERC4626 vault deposit reverts — which an unprivileged vault user can induce by saturating the vault's deposit cap — the repayment cannot be executed at all, the borrower keeps outstanding `borrowerPrincipal`/`borrowerInterest*`, and `IdleCDOEpochVariant._stopEpoch` treats the facility as defaulted.

### Finding Description
`_repay` pulls the repayment from the borrower and clears `borrowerInterestDebt` → `borrowerInterestAccrued` → `borrowerPrincipal`, then re-deploys the cash:

- `contracts/strategies/idle/ProgrammableBorrower.sol:466-533` — `_repay` ends with `if (epochAccountingActive) { _depositToVault(totalRepaidAssets, ...) }` (line 524-527), and the partial-fronted-debt path does `_depositToVault(assets, assets)` at line 499.
- `_depositToVault` (lines 378-385) calls `vault.deposit(_assetAmount, address(this))` with no `try/catch` and no check against `vault.maxDeposit(address(this))`.
- `repay()`/`executeRepay()` (lines 419-432) both route to `_repay`, so there is no alternative path that clears debt without a vault deposit while `epochAccountingActive` is true. `setVault`/`emergencyExitVault` are also blocked while an epoch is active (`epochAccountingActive` guard, line 165) and cannot unwind this.
- In `IdleCDOEpochVariant._stopEpoch` (`contracts/IdleCDOEpochVariant.sol:395-404`), `onStopEpoch` returning `false` triggers `_handleBorrowerDefault`; in `ProgrammableBorrower.onStopEpoch` (lines 235-237) any outstanding `borrowerPrincipal`/`borrowerInterestDebt`/`borrowerInterestAccrued` in close-pool mode (`_isRequestingAllFunds`) returns `false`.

So: while an epoch is running, `repay` is impossible whenever `vault.deposit` reverts. `deposit` is the only vault call in the repay path that is *not* wrapped (contrast `onStopEpoch` lines 246-253, where `vault.withdraw` is `try/catch`ed precisely because external vault failures shouldn't brick epoch settlement). An unprivileged third party who is a user of the configured ERC4626 vault (allowed attacker class) can keep deposits blocked — e.g., saturating a per-market/per-vault supply cap so `deposit` reverts — throughout the epoch-end window. The honest borrower, despite holding the funds and calling `repay(0)`, remains on the books as owing principal + interest. When the manager then calls `stopEpoch(0, 1)` to close the pool, `onStopEpoch` returns `false` and the solvent borrower is marked in default; withdraw claims are settled through the default/recovery multiplier path instead of a clean close.

The same unchecked call exists in `onStartEpoch` (line 216): `_depositToVault(underlyingToken.balanceOf(address(this)), 0)` reverts inside `startEpoch` if the vault rejects the deposit, blocking the epoch restart.

### Impact Explanation
- A solvent borrower is forced into `_handleBorrowerDefault`: the pool records a borrower default, `defaulted = true`, `epochNumber`-based recovery claims are applied and the honest borrower's repaid-but-not-recorded funds are never pulled. Loss for lenders equals the unrecovered `borrowerPrincipal + accrued interest` discounted by the recovery multiplier; the borrower also suffers default treatment despite having attempted repayment.
- Blocking `onStartEpoch`'s deposit reverts `startEpoch`, temporarily freezing the pool in the buffer phase (no new epoch can start while the vault refuses deposits), denying lenders epoch interest accrual for the duration.

### Likelihood Explanation
Requires an ERC4626 vault whose `deposit` can revert under third-party influence (supply caps, per-receiver `maxDeposit`, paused vaults) — a standard property of many ERC4626 vaults. The attacker needs only to be a vault depositor and must keep the deposit path blocked across the epoch-end window. No privileged role needed. Cost is the attacker's deposit capital, which remains withdrawable, making this a cheap griefing attack with real loss forced onto lenders/borrower.

### Recommendation
Mirror the fix from the original report — check viability of the second step before committing, or make it non-blocking:
- In `_depositToVault`, wrap `vault.deposit` in `try/catch` (or check `vault.maxDeposit(address(this))` first) and fall back to keeping the repaid cash on-hand in the contract; `availableToBorrow`, `onStopEpoch`'s `onHand` accounting, and `totalUnderlying` already include idle token balance, so holding cash is accounting-safe.
- Apply the same treatment to the `_depositToVault` call inside `onStartEpoch` so a vault deposit failure cannot block `startEpoch`.

### Proof of Concept
Foundry fork test outline (pattern after `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
// Setup per existing harness: owner deposits AA, manager startEpoch, borrower borrows.
idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);
vm.prank(revolvingBorrower);
programmableBorrower.borrow(drawAmount);

// ATTACK (unprivileged vault user): saturate the ERC4626 vault deposit cap so
// vault.deposit() reverts for ProgrammableBorrower.
deal(USDC, attacker, vault.maxDeposit(address(programmableBorrower)), true);
vm.prank(attacker);
vault.deposit(vault.maxDeposit(address(programmableBorrower)), attacker);
// vault now returns maxDeposit == 0 / reverts on any deposit.

// Honest borrower attempts full repayment during the running epoch.
uint256 totalOwed = programmableBorrower.borrowerPrincipal()
    + programmableBorrower.borrowerInterestOwedNow();
deal(USDC, revolvingBorrower, totalOwed, true);
vm.prank(revolvingBorrower);
vm.expectRevert(); // vault.deposit reverts -> whole repay reverts
programmableBorrower.repay(0);

// Debt is still outstanding; close-pool stop marks the solvent borrower defaulted.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(0, 1); // onStopEpoch returns false -> _handleBorrowerDefault
assertTrue(cdoEpoch.defaulted());
```