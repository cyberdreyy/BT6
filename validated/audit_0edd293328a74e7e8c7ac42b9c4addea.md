### Title
Vault liquidity draining makes `stopEpoch` permanently revertible, freezing tranche LP withdrawals — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The SSRF bug class — the system fetches an attacker-influenced external resource and trusts the result — maps to `ProgrammableBorrower.onStopEpoch`, where IdleCDO's epoch settlement depends on an external ERC4626 vault that unprivileged third parties (any vault user) can interact with. An attacker who is merely a user of that vault can drain or cap its withdrawable liquidity at epoch end, causing `vault.withdraw` to revert inside `onStopEpoch` and making `stopEpoch` fail indefinitely, freezing all pending LP withdrawals.

### Finding Description
`IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)` and only proceeds to `getFundsFromBorrower`, `collectWithdrawFunds`, and `_updateAccounting` if it returns true [1](#0-0) . Inside `onStopEpoch`, when on-hand cash is below `_amountRequired`, the contract attempts `vault.withdraw(shortfall, ...)`; any revert is caught and rethrown as `StopEpochVaultLiquidityUnavailable` [2](#0-1) .

The only pre-check is `shortfall > _currentVaultAssets()` — i.e., it verifies the shares are *economically* sufficient, not *withdrawable*. For the overwhelmingly common class of ERC4626 lending vaults (Morpho/AAVE-style, or any vault with utilization), `convertToAssets` can be fully solvent while `maxWithdraw` ≈ 0 because the vault's assets are lent out. Any unprivileged vault user can push utilization to ~100% (borrow or withdraw the idle side of the underlying vault) at negligible or zero capital cost — borrowing is fully collateralized and can be unwound later.

Sequence (running epoch, programmable mode):
1. Attacker (plain EOA) borrows/withdraws nearly all liquidity from the underlying ERC4626 vault shortly before `epochEndDate`.
2. `stopEpoch` is called; `totalInterestDueNow`/`_vaultNetInterest` still reports full value via `convertToAssets` [3](#0-2) , so `shortfall <= _currentVaultAssets()` and the guard at line 245 does not return early.
3. `vault.withdraw(shortfall, ...)` reverts → `StopEpochVaultLiquidityUnavailable` bubbles → the whole `stopEpoch` transaction reverts.
4. The epoch cannot stop: `isEpochRunning` stays true, `claimWithdrawRequest` payouts are never funded via `collectWithdrawFunds`, and `claimInstantWithdrawRequest`/`getInstantWithdrawFunds` flows are blocked because the epoch never transitions.
5. The attacker repeats or sustains the liquidity squeeze as long as desired (lending-vault utilization can be kept at ~100% indefinitely by rolling the borrow).

Broken invariant: solvency/liveness of the epoch state machine — settlement must not be hostage to third-party liquidity state that `_currentVaultAssets()` cannot observe.

Existing guards do not stop it: `epochAccountingActive`/`epochPendingWithdraws` gating is unrelated; the honest owner/manager cannot force-settle because every `stopEpoch` call hits the same revert; the default path (`_handleBorrowerDefault`) is unreachable because the hook reverts rather than returning `false` [4](#0-3) ; `emergencyExitVault` is also subject to the same vault illiquidity.

### Impact Explanation
Temporary freezing of all tranche-holder funds and pending withdraw requests for the duration of the liquidity squeeze. Concretely: with P = pending withdraws and I = epoch interest, at least `P` of matured withdraw requests and the full pool NAV (~`totBorrowed + I`, e.g., tens of millions USDC in a live facility) cannot be settled or claimed while the attacker keeps the ERC4626 vault illiquid. Cost to attacker is only gas plus the opportunity/borrow cost of a fully collateralized vault position, which is recoverable. Because the failure mode is a revert (not a false return), there is no upper bound on freeze duration and no on-chain fallback path to settle the epoch.

### Likelihood Explanation
Any EOA can interact with a public ERC4626 lending vault; timing the drain to just before `epochEndDate` is trivial since `epochEndDate` is public. Motivated attackers include short sellers of the tranche tokens, competing credit facilities, or attackers combining this with a borrow-side position to extract the honest borrower's collateral terms. The condition `shortfall <= _currentVaultAssets()` holds naturally whenever the vault is solvent-but-illiquid, which is the normal end state of a drained lending vault — no exotic preconditions.

### Recommendation
Check withdrawable liquidity, not just asset value, before attempting the withdrawal: use `vault.maxWithdraw(address(this))` (as the invariant handler already models) and, when `shortfall > maxWithdraw`, return `false`/`true` deterministically so IdleCDO can route to the existing default/settlement path instead of reverting. Alternatively, withdraw `min(shortfall, maxWithdraw)` and let the subsequent `transferFrom` shortfall fall into `_handleBorrowerDefault` semantics, and/or add a governance escape that can close the epoch when `maxWithdraw` is insufficient.

### Proof of Concept
```solidity
// test/foundry/StopEpochVaultLiquidityFreeze.t.sol
function test_stopEpoch_freezesWhenVaultLiquidityDrained() public {
    // Setup: IdleCDOEpochVariant + ProgrammableBorrower, underlying ERC4626
    // lending vault (e.g., a Morpho-style vault or MockInvariantVault with setWithdrawLimit).
    // 1. LPs deposit, requestWithdraw(P), epoch is running, epochEndDate reached.
    vm.warp(epochEndDate + 1);

    // 2. Attacker (arbitrary EOA / vault user) drains vault liquidity:
    //    on a lending vault: borrow all available; on mock: setWithdrawLimit(0).
    vault.setWithdrawLimit(0); // stands in for borrow-all-utilization

    // 3. ProgrammableBorrower still reports full solvency via convertToAssets,
    //    so shortfall <= _currentVaultAssets() and the early-return guard is skipped.
    uint256 amountRequired = pendingWithdraws + interest; // > onHand
    assertLe(amountRequired - onHand, borrower.vaultSharesBalance() /* as assets */);

    // 4. stopEpoch reverts every time; epoch cannot settle; claims stay frozen.
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdo.stopEpoch(0, 0);

    // Repeating after time passes still fails while attacker keeps utilization ~100%.
    vm.warp(block.timestamp + 30 days);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdo.stopEpoch(0, 0);

    // claimWithdrawRequest for requester reverts / pays 0 — funds frozen.
}
```

### Citations

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L337-347)
```text
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```
