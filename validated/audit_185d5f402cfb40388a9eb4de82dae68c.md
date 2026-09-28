### Title
External vault liquidity griefing permanently/temporarily blocks `stopEpoch` for programmable-borrower pools — ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
The Groovy sandbox bypass report is about an attacker abusing a dynamically-managed execution surface that the system trusts. The closest analog in idle-tranches is `ProgrammableBorrower`'s dynamically-configured ERC4626 `vault`: `IdleCDOEpochVariant._stopEpoch` unconditionally delegates liquidity provisioning to `onStopEpoch`, which calls `vault.withdraw` and reverts with `StopEpochVaultLiquidityUnavailable` whenever the external vault cannot serve the withdrawal. Any unprivileged user of that shared ERC4626 vault (e.g., a Morpho borrower or redeemer) can drain its available liquidity so `stopEpoch` always reverts, freezing all LP funds in the pool for as long as liquidity stays exhausted.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower(_borrower()).onStopEpoch(...)` and lets hook reverts bubble (`contracts/IdleCDOEpochVariant.sol:395-404`). In `ProgrammableBorrower.onStopEpoch`, when on-hand cash is below `_amountRequired` and vault shares economically cover the shortfall, `vault.withdraw(shortfall, ...)` is executed in a `try/catch` that reverts `StopEpochVaultLiquidityUnavailable` on failure (`contracts/strategies/idle/ProgrammableBorrower.sol:239-253`).

For utilization-based vaults (Morpho-style), withdrawable liquidity equals `totalAssets - totalBorrowed`. An attacker who is an ordinary vault user can borrow (or sandwich-redeem) essentially all idle liquidity. Every subsequent `stopEpoch`/`stopEpochWithDuration`/`closePool` attempt reverts, because:

- `_beforeStopEpoch` and the recall rely on this same hook; there is no fallback path that pulls `onHand` first and retries the remainder later.
- The revert happens before `collectWithdrawFunds`, `_updateAccounting`, or any state cleanup, so `isEpochRunning` stays true, deposits stay paused, and both pending withdraw receipts and active tranche redemptions stay frozen.
- The attack is repeatable each block: the attacker only needs to keep vault liquidity below `shortfall` around the honest manager's stopEpoch transactions, or borrow the vault's free liquidity outright.

### Impact Explanation
Temporary-to-indefinite freezing of all pool funds. All AA/BB principal and yield in the epoch (arbitrary magnitude — the full `getContractValue`) cannot be settled while the external vault remains illiquid. Unlike a normal vault illiquidity, the CDO cannot even pull the cash that *is* on hand at the borrower contract, because the revert aborts the entire stop flow atomically. Loss is opportunity cost of the full NAV plus, if sustained past borrower repayment deadlines, a forced transition toward default accounting.

### Likelihood Explanation
Requires no privileges: the attacker only needs to be a user of the shared ERC4626 vault, an actor explicitly in scope. Cost is the interest on a flash- or short-duration borrow against the vault timed around epoch end (which is publicly known via `epochEndDate`). For a heavily-utilized vault the condition can even occur organically.

### Recommendation
In `onStopEpoch`, when `vault.withdraw` fails or `maxWithdraw` is below the shortfall, withdraw what is available (or skip the withdrawal) and return `true` so IdleCDO's `transferFrom` pulls whatever liquidity exists; let the genuine shortfall fall into the existing `_handleBorrowerDefault` path rather than a blocking revert. Alternatively, make IdleCDO pull available `onHand` first and treat only the uncovered residual as retryable/defaultable.

### Proof of Concept
```solidity
// Fork test sketch (Foundry, Morpho-style vault as in test/foundry/ProgrammableBorrowerCreditVault.t.sol)
// 1. Deploy pool with programmable borrower + real external vault; LP deposits; startEpoch.
idleCDO.depositAA(10_000e6);
cdoEpoch.startEpoch();

// 2. Attacker (ordinary vault user) drains vault liquidity.
vault.borrow(vault.totalAssets() - vault.totalBorrowed()); // or redeem all idle liquidity

// 3. After epochEndDate, every manager stopEpoch reverts.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
cdoEpoch.stopEpoch(newApr, 0);

// 4. Repeat each time vault liquidity refills -> epoch never stops, LP funds frozen.
assertTrue(cdoEpoch.isEpochRunning());
```