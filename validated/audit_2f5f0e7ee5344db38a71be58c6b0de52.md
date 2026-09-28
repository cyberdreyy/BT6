### Title
A single reverting/illiquid ERC4626 vault permanently DoSes `stopEpoch`, freezing all LP funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](https://github.com/EzraCole/idle-tranches--017/blob/main/contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
Analogous to the Notional rebalance DoS, `IdleCDOEpochVariant._stopEpoch` calls the programmable borrower's `onStopEpoch` hook, which must withdraw liquidity from a single external ERC4626 vault. Both the vault share valuation (`convertToAssets` via `_currentVaultAssets`) and the liquidity recall (`vault.withdraw`) revert-bubble through `onStopEpoch` back into `_stopEpoch`. Because the revert path is intentionally not caught (unlike `getFundsFromBorrower`, which is wrapped in try/catch), there is no fallback: `_handleBorrowerDefault` is only reached when `onStopEpoch` *returns* `false`, not when it reverts. An unprivileged user of the ERC4626 vault can keep the vault's idle liquidity drained (withdraw/borrow all available assets) so that `vault.withdraw(shortfall)` always reverts, permanently blocking `stopEpoch` and freezing every withdraw request and LP redemption.

### Finding Description
`ProgrammableBorrower.onStopEpoch` performs three vault-touching operations before returning success:
1. `_currentVaultAssets()` calls `vault.convertToAssets(shares)` — a revert (vault paused, upgrade, or a vault that reverts on valuation under stress) bubbles straight up. [1](#0-0) 
2. `vault.withdraw(shortfall, ...)` inside a try/catch that re-throws `StopEpochVaultLiquidityUnavailable`. [2](#0-1) 
3. The caller in `_stopEpoch` deliberately lets hook reverts bubble ("Hook reverts bubble so transient ERC4626 liquidity failures can be retried"). [3](#0-2) 

The default-handling path (`_handleBorrowerDefault`) is reached only when the hook *returns* `false` (line 236, `borrowerPrincipal != 0` on close) or when `getFundsFromBorrower` throws inside the try block — never when the vault itself reverts. So a vault whose `withdraw` reverts because its idle liquidity is exhausted produces an unconditional `stopEpoch` revert.

Additionally, `_resolveStopEpochInterest`/`totalInterestDueNow` reads vault PnL (`vaultInterestAccrued` uses `convertToAssets`), so a vault that reverts on valuation breaks `stopEpoch` even when no cash is needed from the borrower.

Attack scenario (running epoch, fixed-APR or minted mode with `pendingWithdraws > 0`):
- Attacker is an ordinary depositor/user of the ERC4626 vault (the prompt's "user of the programmable borrower's ERC4626 vault" actor) — no IdleCDO privileges required.
- Attacker ensures `vault.maxWithdraw(programmableBorrower)` < shortfall by withdrawing the vault's idle liquidity (for Morpho/ERC-4626-style vaults: borrow out available assets; atomically repeatable each block).
- Owner/manager calls `stopEpoch` → `onStopEpoch` → `vault.withdraw(shortfall)` reverts → `StopEpochVaultLiquidityUnavailable` → whole `_stopEpoch` reverts. `isEpochRunning` stays true forever while the attacker keeps liquidity drained.
- For vaults that revert `convertToAssets` when paused/compromised (the "external protocol paused/compromised" case from the original report), the DoS is permanent with zero attacker cost.

### Impact Explanation
While `stopEpoch` reverts, `isEpochRunning` remains true, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` remain false, `_pause()` is active, and all pending withdraw receipts (`pendingWithdraws`) are never funded — `collectWithdrawFunds` is only called on the success path. All LP principal in the vault sleeve plus owed interest is frozen for the duration of the DoS; for a permanently reverting vault (paused, bricked upgrade), the freeze is permanent until `emergencyExitVault`/`rescueToken` owner intervention — and even then accounting (`bufferStartVaultAssets`, epoch baselines) cannot recover cleanly because `onStopEpoch`/`totalInterestDueNow` remain uncallable. Quantified: the entire CDO NAV (`getContractValue()`) plus `pendingWithdraws` is unclaimable. This matches the report's core harm: inability to exit a compromised/failing external market and forced suboptimal allocation.

### Likelihood Explanation
High relative to assumptions: the facility design deliberately parks idle capital in a third-party ERC4626 vault that IdleCDO does not control. Any vault depositor can transiently drain its liquidity at will (no cost beyond capital, which flash-loan-scale liquidity makes cheap per call), and vault pauses/upgrade failures happen without any attacker action. Unlike Notional where the fix could wrap calls in try/catch, here the revert is *deliberately* bubbled, so every single stopEpoch attempt deterministically fails while the condition holds.

### Recommendation
Make `onStopEpoch` failure-tolerant rather than revert-propagating: catch `StopEpochVaultLiquidityUnavailable` (and `convertToAssets` failures) inside `onStopEpoch` and return `false` so IdleCDO routes through `_handleBorrowerDefault`, or add a CDO-side `try` around the hook that treats hook reverts as a default. Alternatively, give `stopEpoch` a "force default" mode letting the owner declare default when vault recall is impossible, so LP funds route to `DefaultDistributor` recovery instead of freezing.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: deploy IdleCDOEpochVariant + IdleCreditVault + ProgrammableBorrower,
// vault = real ERC4626 (e.g. a Morpho vault) holding borrower-parked funds.
// 1. LP deposits (KYC'd lender) -> buffer -> startEpoch() -> onStartEpoch deposits
//    all idle cash into the vault.
// 2. Attacker (plain ERC4626 vault user) borrows/withdraws vault idle liquidity so
//    vault.maxWithdraw(address(programmableBorrower)) < pendingWithdraws + interest.
//    Keep it drained via MEV each block.
// 3. Warp past epochEndDate. Owner calls stopEpoch(0, 0):
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
cdo.stopEpoch(0, 0);
// 4. Invariant broken: isEpochRunning stays true; pendingWithdraws unfunded;
//    claimWithdrawRequest reverts for every LP indefinitely.
assertTrue(cdo.isEpochRunning());
vm.expectRevert();
vault.claimWithdrawRequest(lp);
```

Key PoC detail: ensure `shortfall <= _currentVaultAssets()` so the code reaches the `vault.withdraw` call (otherwise it returns `true` and fails later at `transferFrom` → default path, which is the intended fallback and *not* a bug). The vulnerability exists precisely in the window where vault shares cover the shortfall on paper but the withdrawal reverts, or where `convertToAssets`/`totalInterestDueNow` itself reverts.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L246-253)
```text
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-404)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }
```
