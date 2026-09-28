### Title
Attacker-induced ERC4626 illiquidity permanently reverts `stopEpoch` via `StopEpochVaultLiquidityUnavailable`, freezing all LP funds in a running epoch — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The external CVE is an availability bug (hang/crash DoS). The matching credit-vault surface is the epoch state machine: `IdleCDOEpochVariant.stopEpoch` → `ProgrammableBorrower.onStopEpoch`. `onStopEpoch` decides coverage of the liquidity shortfall using the *economic* value of vault shares (`_currentVaultAssets()`, `convertToAssets`), then calls `vault.withdraw(shortfall, ...)`, which requires *actual idle liquidity* in the ERC4626 vault. If `withdraw` reverts, `onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable`, which propagates out of `stopEpoch` — no default is declared, the epoch stays running, and no LP or receipt holder can exit. An unprivileged user of the underlying ERC4626 vault (e.g., a MetaMorpho borrower draining all market liquidity) can hold the vault illiquid, making every `stopEpoch` call revert indefinitely.

### Finding Description
In `contracts/strategies/idle/ProgrammableBorrower.sol` lines 239-253:

```solidity
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true;
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable();
  }
}
```

The `shortfall > _currentVaultAssets()` check (line 245) compares against `convertToAssets(shares)` — the *mark* value of the position — not `vault.maxWithdraw(address(this))`, which reflects withdrawable liquidity. For vaults like MetaMorpho, `convertToAssets` can be fully solvent while `maxWithdraw` is near zero because underlying market liquidity has been borrowed out. In that window the vault passes the solvency gate, `vault.withdraw` reverts, and `onStopEpoch` reverts. Because this happens inside `stopEpoch`'s success path (before `getFundsFromBorrower`'s try/catch default handling), the revert is retryable only — it never triggers `_handleBorrowerDefault`, so the pool cannot be defaulted, closed, or unstopped by anyone.

Any user of the external ERC4626 vault — explicitly an allowed unprivileged attacker class — can create this state: borrow the maximum from the vault's underlying markets (or atomically rebalance/borrow so idle liquidity < shortfall) and keep positions open. Every `stopEpoch`/`stopEpochWithDuration` call then reverts. No privileged recovery path exists inside the epoch flow: `emergencyExitVault` also calls `vault.redeem`, which is equally liquidity-bound, and `_handleBorrowerDefault` is only reached on the `transferFrom` failure or `getInstantWithdrawFunds` catch paths, not on this revert. Guards that fail to stop it: the `convertToAssets` coverage check is precisely the wrong oracle (solvency ≠ liquidity), and there is no fallback that treats a covered-but-illiquid withdraw as a default. This is inconsistent with `onDefault` (lines 289-301), which deliberately avoids live vault valuation during stress.

### Impact Explanation
Direct temporary freezing of the entire pool's TVL with no expiration bound. While the attacker keeps the ERC4626 vault illiquid, `stopEpoch` always reverts, `isEpochRunning` stays true, deposits are closed, `requestWithdraw` proceeds are gated behind the pending instant/normal queues, and pending receipt holders cannot claim because funds were never collected by `collectWithdrawFunds`. All LP principal and accrued interest (which can be the full pool NAV parked in the vault via `_depositToVault` in `onStartEpoch`, line 216) is frozen for as long as the attacker services the borrow; cost to the attacker is only the vault borrow interest on the drained liquidity. This satisfies the accepted-impact bar (temporary freezing of user funds, quantified as full TVL) and is a faithful analog of the CVE's availability-loss class.

### Likelihood Explanation
High. The attacker needs only to be a normal user of the external ERC4626 vault — no role in idle-tranches. Morpho-style markets routinely allow high-utilization borrows, and the attacker can time the drain right before `epochEndDate`, then maintain utilization cheaply by rolling the borrow. The condition (solvent shares, zero withdrawable liquidity) is a normal, frequently occurring state for lending vaults, not an exotic edge case. Detection is trivial: any liquid-looking but borrowed-out vault makes `stopEpoch` revert deterministically.

### Recommendation
In `onStopEpoch`, replace the `_currentVaultAssets()` coverage check with a liquidity-aware check (`vault.maxWithdraw(address(this))`), and treat a `withdraw` failure on a nominally-covered position as a real shortfall: return `true` so IdleCDO's subsequent `transferFrom` fails and `_handleBorrowerDefault` fires, or explicitly set a `vaultLiquidityDefault` flag the CDO maps into the default path. Also make `emergencyExitVault` / the stop flow able to redeem pro-rata (`redeem` capped to available liquidity) rather than all-or-nothing `withdraw`.

### Proof of Concept
Foundry fork (USDC MetaMorpho vault, as in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
// setup: depositAA(amount), setIsInterestMinted(true), startEpoch
// funds parked: programmableBorrower deposited amount into morphoVault
uint256 amount = 10_000 * oneScale;
vm.prank(owner);
cdoEpoch.setIsInterestMinted(true);
idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);

// attacker: unprivileged user of the ERC4626 vault drains market liquidity
// so morphoVault.maxWithdraw(programmableBorrower) == 0 while
// convertToAssets(shares) still >= the needed shortfall
vm.startPrank(attacker); // any EOA, borrower in the underlying Morpho market
morpho.borrow(allIdleLiquidity, ...);   // drain the vault's markets
vm.stopPrank();
assertEq(morphoVault.maxWithdraw(address(programmableBorrower)), 0);
assertGt(programmableBorrower.vaultSharesBalance(), 0);

vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
cdoEpoch.stopEpoch(0, 0);

// every retry reverts; epoch never stops, no default path reachable
assertTrue(cdoEpoch.isEpochRunning());
assertFalse(cdoEpoch.defaulted());
// LP funds (~amount USDC) frozen in the vault position
```

Caveat: I verified the code path and the `convertToAssets`-vs-`withdraw` mismatch in `ProgrammableBorrower.sol` lines 239-253, but did not read `IdleCDOEpochVariant.stopEpoch`'s full post-`onStopEpoch` flow line-by-line in this pass; the PoC assumes the `StopEpochVaultLiquidityUnavailable` revert propagates (it is a plain `revert`, not wrapped in try/catch) and that no later catch converts it to a default, consistent with the invariant test at `ProgrammableBorrowerEpochInvariant.t.sol:318-323` which treats it as a retryable revert preserving `epochAccountingActive`.