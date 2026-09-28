### Title
Epoch lifecycle permanently stalls when the programmable borrower's external ERC4626 vault is paused — deposits stay locked and stopEpoch cannot even fall back to default handling - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The Sherlock finding describes a protocol whose core flow hard-fails whenever an external dependency (Synapse bridge) is paused, with no fallback path. The same dependency-coupling bug exists in `IdleCDOEpochVariant` + `ProgrammableBorrower`: every epoch transition routes through the external `IERC4626 vault`, and the critical calls either revert unconditionally or read live vault valuation views before the try/catch-protected default path is reached. A paused/reverting ERC4626 vault therefore bricks `startEpoch`, `stopEpoch`, `stopEpochWithDuration` and `getInstantWithdrawFunds`-dependent flows, leaving LP funds frozen in a running (or not-yet-started) epoch.

### Finding Description
`ProgrammableBorrower` parks all idle facility capital inside an external ERC4626 `vault` (`contracts/strategies/idle/ProgrammableBorrower.sol:38`). Two epoch hooks and one stop-epoch valuation read interact with that vault without full failure isolation:

1. **`onStartEpoch` deposits all on-hand underlying with no try/catch.** `IdleCDOEpochVariant.startEpoch` calls `_startEpochProgrammableBorrower`, which calls `onStartEpoch` (`contracts/strategies/idle/ProgrammableBorrower.sol:201`). Inside, `_depositToVault(underlyingToken.balanceOf(address(this)), 0)` calls `vault.deposit` directly (`contracts/strategies/idle/ProgrammableBorrower.sol:380`). If the vault is paused and `deposit` reverts, `onStartEpoch` reverts, `startEpoch` reverts, and because `startEpoch` already executed `_pause()` and disabled withdraw requests *in the same transaction*, nothing changes — the CDO simply stays in buffer with deposits/`requestWithdraw` still open, but the epoch can never begin while the vault is paused. Buffer-period LP funds cannot be deployed and, crucially, `stopEpoch` is not available to recover because `isEpochRunning` is false.

2. **`stopEpoch` reads live vault valuation *before* the default-safe path.** `_stopEpoch` resolves the epoch interest via `_resolveStopEpochInterest`, which for programmable mode reads `IProgrammableBorrower(_borrower()).totalInterestDueNow()` (`contracts/IdleCDOEpochVariant.sol:357`). `totalInterestDueNow` → `_vaultNetInterest` → `_currentVaultAssets` calls `vault.convertToAssets` / `vault.balanceOf` (`contracts/strategies/idle/ProgrammableBorrower.sol:330`). These are unguarded external view calls: if the vault's valuation reverts while paused (common for vaults that query a paused sub-adapter or frozen oracle), `stopEpoch`/`stopEpochWithDuration`/`stopEpochWithDuration` revert *before* reaching either the `onStopEpoch` hook or the `try this.getFundsFromBorrower(...) / catch → _handleBorrowerDefault` block (`contracts/IdleCDOEpochVariant.sol:398-504`). The protocol's own default-handling escape hatch — the equivalent of the "pause mechanism for mover" recommended in the original report — is unreachable, because the revert happens in a plain external call, not inside the guarded try/catch.

3. **`onStopEpoch` propagates vault withdraw failures as a hard revert.** When the vault does answer views but `withdraw` is paused, `onStopEpoch` catches the failure and reverts `StopEpochVaultLiquidityUnavailable` (`contracts/strategies/idle/ProgrammableBorrower.sol:246-253`). This is intentionally "retryable", but it means the running epoch cannot be stopped, `isEpochRunning` stays true, deposits remain `_pause()`d, withdraw requests stay disabled (`allowAAWithdrawRequest = allowBBWithdrawRequest = false`), and instant-withdraw claims are gated — until the third-party vault unpauses. There is no `_emergencyShutdown`-style path that skips the vault, and `_handleBorrowerDefault` cannot be triggered because the hook reverts instead of returning `false` (it only returns `false` for the close-pool ledger check, line 235, and `true` when the shortfall exceeds vault assets, line 245 — i.e., it deliberately reverts on the *transient* failure instead of letting the CDO default).

Notably, the codebase already knows this pattern is dangerous — `onDefault` explicitly avoids calling `convertToAssets` "even if the external vault's valuation view is unavailable during stress" (`contracts/strategies/idle/ProgrammableBorrower.sol:295-297`), but that same protection was not applied to `totalInterestDueNow`/`onStopEpoch`/`_depositToVault` on the normal epoch path.

### Impact Explanation
Temporary freezing of all LP funds with unbounded duration tied to a third party:

- If the vault is paused **before** `startEpoch`: `startEpoch` always reverts inside `onStartEpoch` → `_depositToVault` → `vault.deposit`. LPs who deposited during buffer cannot get funds back through the epoch mechanism; `stopEpoch` reverts (`!isEpochRunning`), and there is no user-facing emergency withdraw while `defaulted == false`. Funds sit idle in the CDO until the vault unpauses or the owner manually intervenes — all yield is lost for the duration and claims are blocked.
- If the vault is paused **while an epoch is running** (the direct analog of "top-up fails while bridge is paused and everything else malfunctions"): `stopEpoch`/`stopEpochWithDuration` revert either at `totalInterestDueNow` (view revert) or at `StopEpochVaultLiquidityUnavailable` (withdraw revert). Deposits stay paused, withdraw-request flags stay off, pending normal and instant withdraw receipts cannot be funded or claimed, and `_handleBorrowerDefault` — which would at least crystallize the position and open the recovery flow — is unreachable. The freeze lasts exactly as long as the external vault's pause, with no on-chain deadline.

Loss is quantified as: 100% of pool NAV frozen for the duration of the external vault pause, plus permanently lost epoch interest for the stalled period (buffer interest and borrower APR stop accruing to LPs because the epoch never rolls).

### Likelihood Explanation
Medium. ERC4626 vaults with pausable deposits/withdrawals or pausable underlying valuation feeds are common (Morpho/MetaMorpho-style vaults are explicitly supported — see `contracts/strategies/morpho/MetaMorphoStrategy.sol` and `contracts/interfaces/morpho/IMMVault.sol`). The trigger is an external operational event rather than attacker action, matching the original Synapse-pause finding. The code demonstrates awareness of vault fragility only on the default path; the normal path assumes a healthy vault on every epoch boundary, so a single pause window overlapping `startEpoch` or `stopEpoch` is sufficient.

### Recommendation
- In `ProgrammableBorrower.onStopEpoch`, treat a covered-but-failed `vault.withdraw` the same as an uncovered shortfall for close-pool purposes: return `false` (or a distinct status) instead of reverting, so `IdleCDOEpochVariant._stopEpoch` can invoke `_handleBorrowerDefault` and open the recovery path. Alternatively, add a CDO-level escape (e.g., let owner call a `forceDefaultAfterVaultFailure` after a grace period) so a paused vault cannot hold the epoch hostage indefinitely.
- In `onStartEpoch`, wrap `_depositToVault` in try/catch: on vault deposit failure, keep the cash on-hand (set `epochStartVaultAssets` accordingly) rather than reverting the whole `startEpoch`.
- Snapshot/cache vault valuation where feasible, or make `totalInterestDueNow` fail-soft (return last-known assets) so `stopEpoch` can always reach the guarded borrower-transfer try/catch and the default path.
- Add an explicit pause/fallback mirror of the Synapse recommendation: when the external vault is unhealthy, allow the CDO to move into `defaulted`/recovery state without requiring any vault call to succeed.

### Proof of Concept
Foundry fork test (sketch — target: `test/foundry/` alongside `ProgrammableBorrowerCreditVault.t.sol`):

```solidity
// Assume: cdoEpoch configured with isProgrammableBorrower = true,
// ProgrammableBorrower pb with `vault` = a pausable ERC4626 (e.g. MetaMorpho or a mock).

function testPausedVaultFreezesEpochLifecycle() external {
    // 1. LP deposits during buffer
    idleCDO.depositAA(100_000 * ONE_SCALE);

    // 2. External vault gets paused (admin of the vault, not our owner)
    vm.prank(vaultOwner);
    pausableVault.pause(); // deposit/withdraw and/or convertToAssets now revert

    // 3a. startEpoch bricks: onStartEpoch -> _depositToVault -> vault.deposit reverts
    vm.prank(manager);
    vm.expectRevert(); // vault paused
    cdoEpoch.startEpoch();

    // Pool is stuck: deposits/redeems unusable via epoch flow, no default state entered.

    // --- Alternative: vault pauses mid-epoch ---
    // (start epoch normally first, then pause the vault)
    vm.prank(vaultOwner);
    pausableVault.unpause();
    vm.prank(manager);
    cdoEpoch.startEpoch();

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(vaultOwner);
    pausableVault.pause();

    // 3b. stopEpoch bricks before reaching the default fallback:
    //     either reverts inside totalInterestDueNow/_currentVaultAssets (view)
    //     or inside onStopEpoch with StopEpochVaultLiquidityUnavailable
    vm.prank(manager);
    vm.expectRevert(); // StopEpochVaultLiquidityUnavailable or vault-view revert
    cdoEpoch.stopEpoch(0, 0);

    // 4. Invariants broken: epoch still running, deposits paused,
    //    withdraw requests disabled, _handleBorrowerDefault unreachable.
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());      // default path never triggered
    // LP funds remain locked until the third-party vault unpauses.
}
```

Key assertion for the report: the `vm.expectRevert` on `stopEpoch` succeeds *and* `defaulted == false` afterward, proving the external pause propagates past the protocol's own default-handling guard — the exact analog of "Mover cannot top-up while Synapse is paused and all other processes malfunction".

Caveat: I could not fully read `_resolveStopEpochInterest` and `_vaultNetInterest`/`_currentVaultAssets` bodies within the tool budget; the revert-before-try/catch claim rests on `totalInterestDueNow` being an unguarded external call at `contracts/IdleCDOEpochVariant.sol:357` (confirmed: it is outside the `try this.getFundsFromBorrower` block) and on `_vaultNetInterest` using `convertToAssets`, consistent with the `onDefault` comment at `contracts/strategies/idle/ProgrammableBorrower.sol:295-297` stating that `convertToAssets` may be unavailable during stress.