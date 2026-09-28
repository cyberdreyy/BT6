### Title
Unprivileged ERC4626 vault/market manipulation bricks `stopEpoch` via `onStopEpoch` → `vault.withdraw` revert, freezing the running epoch and all pending withdraws - ([File: contracts/IdleCDOEpochVariant.sol, contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
In programmable-borrower mode, the privileged `stopEpoch`/`stopEpochWithDuration` flow makes a synchronous call into an external endpoint whose liquidity state any unprivileged third party can influence: `IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower.onStopEpoch`, which in turn calls `vault.withdraw(shortfall, ...)` on the configured ERC4626 vault (e.g. a MetaMorpho vault). If that withdrawal reverts because the vault's underlying market liquidity has been drained, `onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable`, and the revert bubbles up uncaught through `_stopEpoch` — the epoch can never be stopped while the condition persists. This mirrors the SSRF bug class: a privileged component dereferences an attacker-influenceable external endpoint inside a synchronous privileged call, with no allowlist or fallback, and the failure mode lands on the protocol rather than the attacker.

### Finding Description
`ProgrammableBorrower.onStopEpoch` (`contracts/strategies/idle/ProgrammableBorrower.sol:231-267`) computes the shortfall between the cash `IdleCDO` will pull (`_amountToPullFromBorrower + _pendingWithdraws`, `IdleCDOEpochVariant.sol:398`) and its on-hand underlying balance. It explicitly distinguishes two failure modes:

```solidity
if (shortfall > _currentVaultAssets()) return true;            // economically uncovered → default path
try vault.withdraw(shortfall, address(this), address(this)) {   // covered but reverts → ...
} catch {
    revert StopEpochVaultLiquidityUnavailable();                // hard revert, bubbles to caller
}
```

The first branch is safe: when the vault position does not cover the shortfall it returns `true` and lets the CDO's `transferFrom` fail into `_handleBorrowerDefault`. The second branch — where shares *do* cover the shortfall but `vault.withdraw` cannot deliver assets (MetaMorpho withdraw unwind failure, exhausted market liquidity, vault-level withdraw cap) — reverts. `IdleCDOEpochVariant._stopEpoch` (`IdleCDOEpochVariant.sol:395-404`) wraps only `getFundsFromBorrower` in try/catch; the `onStopEpoch` call is a bare call, so the revert propagates and the entire `stopEpoch` reverts. The code comments ("Hook reverts bubble so transient ERC4626 liquidity failures can be retried") confirm this is by design, but design intent does not bound the freeze duration.

Any user of the ERC4626 vault's underlying markets — explicitly an in-scope attacker class ("a user of the programmable borrower's ERC4626 vault" / "any EOA or contract") — can create exactly this state at negligible net cost:

- Borrow (with collateral on Morpho Blue, recoverable) the available liquidity out of the markets in the vault's supply queue, so `vault.withdraw` reverts while `convertToAssets` still prices the shares at full value (allocated-but-unliquid positions are still counted).
- Or, if the attacker is a large vault shareholder, redeem their own shares to drain the vault's idle/liquid buffer below `shortfall`.

While the condition holds: the epoch stays `isEpochRunning` past `epochEndDate`; `stopEpoch`, `stopEpochWithDuration`, and `stopEpochWithDuration`'s loss path all revert identically; `defaulted` is never set so `finalizeDefault`/recovery flows cannot start; deposits and withdraw requests stay paused (`_pause()` at `startEpoch`, `allowAA/BBWithdrawRequest = false`); and the manager's own `emergencyExitVault` hits the same illiquid `vault.redeem`. The borrower is also blocked from `borrow`/`repay` redeployment paths that touch the vault. Recovery requires the attacker to voluntarily restore liquidity or the markets to naturally regain liquidity.

### Impact Explanation
Temporary freezing of funds, at pool scale. Every LP's tranche tokens are locked in a running-epoch state (no deposits, no withdraw requests, no claims, and no path to `defaulted` finalization) for as long as the attacker keeps the vault's markets illiquid. The frozen principal is the entire programmable-borrower sleeve — all assets parked in the vault plus pending withdraw requests that were supposed to be funded at this stop. The attacker's cost is only the borrow interest on the drained liquidity (or foregone vault yield for the shareholder variant) — far below the frozen TVL — and the collateral is fully recoverable after the grief ends. This is not a "malicious privileged role" or "borrower default" scenario: manager, borrower, and owner all behave correctly and still cannot advance the epoch.

### Likelihood Explanation
Medium. It requires a programmable-borrower deployment (a live, configured mode per `isProgrammableBorrower`/`_checkProgrammableBorrowerMode`), an epoch in the running phase with a nonzero shortfall at stop, and an attacker willing to lock collateral to drain the relevant Morpho markets or hold a dominant vault share. No honest-actor cooperation is needed, the attacker needs no KYC or tranche position, and the same manipulation can be re-applied to every retry of `stopEpoch`. Motivation is griefing/extortion rather than direct profit, which the rules accept under "temporary freezing" since no direct theft occurs.

### Recommendation
Do not let a transient `vault.withdraw` failure make the whole epoch un-stoppable. Concretely:

- In `onStopEpoch`, treat an economically-covered withdrawal revert the same as an uncovered shortfall (return `true` and let the CDO's `transferFrom`/default path decide), or add a `maxShortfall`/`partial withdraw` loop that redeems whatever liquidity is available and returns `false`/`true` based on whether the residual is material.
- Alternatively, add a manager-invokable "force default" path on `IdleCDOEpochVariant` that skips `onStopEpoch` and calls `_handleBorrowerDefault` directly when the hook has been reverting past a deadline, so LP recovery via `finalizeDefault`/`finalizeDefaultRecovery` is reachable even during external-vault illiquidity.
- If `ProgrammableBorrower` ever calls `setVault`, the new vault's redeemability profile becomes a single switch for this attack surface; document or bound which ERC4626 vaults are acceptable (idle-liquidity guarantees, withdraw caps).

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

// Fork test built on test/foundry/ProgrammableBorrowerCreditVault.t.sol setup
// (real MetaMorpho vault `morphoVault` = STEAKHOUSE_USDC, mainnet fork).
function testStopEpochBrickedByVaultLiquidityDrain() external {
    uint256 amount = 10_000 * oneScale;
    uint256 drawAmount = 5_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);          // epoch running; idle funds parked in morphoVault

    vm.prank(revolvingBorrower);
    programmableBorrower.borrow(drawAmount);

    // ── Attacker: unprivileged user of the vault's markets ────────────────────
    // Option A (market borrow): attacker supplies collateral on Morpho Blue and
    // borrows the liquid balance of every market in morphoVault's withdrawQueue,
    // so morphoVault.withdraw() reverts with liquidity shortfall while
    // morphoVault.convertToAssets(programmableBorrower shares) is unchanged.
    _drainMorphoMarketLiquidity(address(morphoVault), attacker);
    // Option B (whale shareholder): attacker redeems enough vault shares so the
    // vault's immediately-withdrawable liquidity < shortfall.
    // ─────────────────────────────────────────────────────────────────────────

    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 pendingW = strategy.pendingWithdraws();
    uint256 shortfall = pendingW - underlying.balanceOf(address(programmableBorrower));
    // Sanity: shares still economically cover the shortfall → onStopEpoch takes the
    // reverting branch, not the `return true` default branch.
    assertLe(shortfall, programmableBorrower.vaultInterestAccrued() + morphoVault.convertToAssets(
        morphoVault.balanceOf(address(programmableBorrower))
    ));

    // Manager tries to stop the epoch — reverts, epoch stays running.
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.isEpochRunning(), "epoch stuck running");
    assertFalse(cdoEpoch.defaulted(), "no default, no recovery path");

    // Retries keep reverting while the attacker maintains illiquidity.
    vm.warp(block.timestamp + 7 days);
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // stopEpochWithDuration / forced-loss path are equally bricked.
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpochWithDuration(0, 0, 30 days, 0);

    // All LP withdraws remain frozen: requests closed, no claim path, and
    // finalizeDefault cannot be reached because `defaulted` was never set.
    assertFalse(cdoEpoch.allowAAWithdrawRequest());
    assertFalse(cdoEpoch.allowBBWithdrawRequest());

    // When the attacker repays the market borrows (collateral recovered),
    // liquidity returns and stopEpoch succeeds — confirming temporary freeze only.
    _restoreMorphoMarketLiquidity(address(morphoVault), attacker);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertFalse(cdoEpoch.isEpochRunning());
}
```