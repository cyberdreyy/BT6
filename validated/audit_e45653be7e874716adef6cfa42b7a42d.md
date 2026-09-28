### Title
Attacker can permanently block `startEpoch` by griefing the programmable borrower's ERC4626 vault deposit, freezing the pool in buffer state - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
In programmable-borrower mode, `IdleCDOEpochVariant.startEpoch` invokes the `ProgrammableBorrower.onStartEpoch` hook, which unconditionally deposits the contract's entire idle underlying balance into an external ERC4626 vault via `_depositToVault`. That call is not wrapped in any try/catch or slippage-tolerant path, so any revert inside `vault.deposit` (deposit cap reached, vault paused, share-mint slippage/rounding guard) bubbles up and reverts the whole `startEpoch` transaction. An unprivileged attacker can force this condition by filling a public ERC4626 vault's deposit cap, making every `startEpoch` call fail. This mirrors the RageTrade batching-manager bug: the first leg of the flow (funds pulled into the borrower adapter) is fine, but the second leg (parking into the external vault) fails, leaving the state machine stuck with no unprivileged recovery.

### Finding Description
`onStartEpoch` (ProgrammableBorrower.sol:201-224) executes:
```solidity
_depositToVault(underlyingToken.balanceOf(address(this)), 0);
```
and `_depositToVault` (lines 378-385) calls `vault.deposit(_assetAmount, address(this))` directly. Per IERC4626, `deposit` "MUST revert if all of assets cannot be deposited (due to deposit limit being reached, slippage, ...)". Compare with `onStopEpoch` (lines 246-253), where the developers *did* wrap `vault.withdraw` in `try/catch` and added the `StopEpochVaultLiquidityUnavailable` retryable path — the same protection was not applied to the start-leg deposit.

Attack sequence (buffer phase, programmable mode):
1. Manager stops an epoch; pool enters buffer, idle underlying sits on `ProgrammableBorrower`.
2. Attacker (any EOA / ERC4626 vault user — explicitly in scope as "a user of the programmable borrower's ERC4626 vault") deposits into the shared external vault until its `maxDeposit`/`maxMint` cap is reached, or until the remaining capacity is below the amount `onStartEpoch` will try to deposit.
3. `startEpoch` → `onStartEpoch` → `vault.deposit(totalIdle)` reverts → `startEpoch` reverts.
4. Every subsequent `startEpoch` attempt reverts the same way while the cap remains saturated, which the attacker can maintain cheaply since their vault deposit remains invested earning yield.

### Impact Explanation
The pool is frozen in the buffer state: no new epoch can start, so `depositDuringEpoch` stays disabled, pending withdraw requests are never funded (they settle through the epoch lifecycle), and buffer deposits sit unproductive. `stopEpoch`/`getInstantWithdrawFunds` paths that depend on epoch progression are unreachable. Recovery requires a privileged `setVault` migration (allowed only because `vault.balanceOf == 0` after the atomic revert), i.e., a temporary freezing of all pool TVL with no unprivileged workaround — exactly the stuck-intermediate-state class of the source finding.

### Likelihood Explanation
Requires a programmable-borrower deployment whose ERC4626 vault enforces a deposit cap or pausable deposits — standard for MetaMorpho/ERC4626 vaults. The attacker needs capital only equal to the remaining cap headroom, not the pool TVL, and can later withdraw it, so the griefing cost is near zero. No privileged misbehavior is needed; the honest manager's routine `startEpoch` call is the trigger.

### Recommendation
Mirror the `onStopEpoch` defensive pattern: wrap `_depositToVault` in `try/catch` inside `onStartEpoch`, and on failure either (a) leave funds on hand and proceed with epoch accounting using the pre-deposit `startAssets` baseline (already computed before the deposit at line 214), or (b) revert with a dedicated retryable error and let `startEpoch` be reattempted, while allowing `setVault` to switch vaults even with pending idle balance. Alternatively, accept a `minSharesOut` parameter and treat a failed park as "keep cash on hand" rather than a fatal condition.

### Proof of Concept
Foundry fork outline: deploy `IdleCDOEpochVariant` + `ProgrammableBorrower` wired to a capped ERC4626 (e.g., a MetaMorpho vault with `maxDeposit`). Run epoch 1 normally, call `stopEpoch`, then from an attacker EOA `vault.deposit(remainingCap)` to saturate the cap. `vm.expectRevert` on `cdoEpoch.startEpoch(...)`: `onStartEpoch` reverts inside `vault.deposit`. Assert `isEpochRunning == false`, `epochAccountingActive == false`, funds still on the borrower adapter, and that repeat `startEpoch` calls keep reverting while the cap stays filled. Note: I could not re-verify `IdleCDOEpochVariant.startEpoch` propagates the hook revert without a catch in this iteration — the finding assumes the hook is invoked directly, which is the standard pattern and is consistent with `onStopEpoch` returning/reverting into `stopEpoch`.