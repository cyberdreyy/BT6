### Title
ERC4626 `convertToAssets` reverts in times of market stress, permanently bricking `onStopEpoch`/`onStartEpoch` and freezing all lender withdrawals - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
Analogous to the Salty `PriceAggregator` that reverts (instead of falling back) when price sources diverge under volatility, `ProgrammableBorrower` unconditionally calls `vault.convertToAssets(shares)` inside `_currentVaultAssets()` on every epoch-state transition and interest-valuation path. When the external ERC4626 vault's valuation view reverts — exactly the condition the external report describes (oracle-dependent pricing failing during volatility) — `IdleCDOEpochVariant.stopEpoch`/`startEpoch` revert, freezing the epoch and all queued/instant lender withdrawals. The codebase already acknowledges this class for the default path (`onDefault` explicitly avoids `convertToAssets` so "CDO default handling must still be able to shut down borrowing"), but the normal stop/start path was left exposed.

### Finding Description
`_currentVaultAssets()` performs an unguarded external call into the strategy's ERC4626 vault: `contracts/strategies/idle/ProgrammableBorrower.sol:546-549` [1](#0-0) 

This helper feeds every valuation and state-transition entry point:

- `onStopEpoch` calls `_accrueBorrowerInterest()`, then `_currentVaultAssets()` twice — once to test whether the shortfall is covered (`shortfall > _currentVaultAssets()`), and again to snapshot `bufferStartVaultAssets`: [2](#0-1) 
- `onStartEpoch` reads `_currentVaultAssets()` to carry buffer-period vault PnL into `bufferedVaultDelta`: [3](#0-2) 
- `_vaultNetInterest()` (and thus `totalInterestDueNow()`, `vaultLoss()`, `vaultInterestAccrued()`) reads it: [4](#0-3) 
- `availableToBorrow()`/`totalUnderlying()` read it: [5](#0-4) 

Per the in-code comment at lines 256–258, `IdleCDOEpochVariant` reads `totalInterestDueNow()` *before* invoking the `onStopEpoch` hook — so a `convertToAssets` revert kills the stop flow at two separate points in the same transaction.

Crucially, the only wrapped external vault call is the liquidity move (`try vault.withdraw(...) catch revert StopEpochVaultLiquidityUnavailable()`), while the *valuation* call is unprotected: [6](#0-5) 

The authors demonstrated awareness of this exact failure mode in `onDefault`, which deliberately skips `convertToAssets` because "Even if the external vault's valuation view is unavailable during stress, CDO default handling must still be able to shut down borrowing": [7](#0-6) 

That same reasoning was not applied to `onStopEpoch`/`onStartEpoch`.

**Attacker path (unprivileged):** the allowed attacker set includes "a user of the programmable borrower's ERC4626 vault". For a MetaMorpho-style vault (the integration used in tests, `STEAKHOUSE_USDC`), `convertToAssets` → `totalAssets()` iterates underlying markets and calls `accrueInterest`, which queries each market's oracle and borrow-rate model. An unprivileged vault-market participant can drive a listed market into a state where `accrueInterest` reverts — e.g., by supplying then borrowing essentially all liquidity of a thin market so utilization hits ~100% and `borrowRateView`/`accrueInterest` overflows or its oracle call fails (a documented MetaMorpho/Morpho fragility under stress). This is the same trigger class as the Salty report: the pricing component fails precisely when volatility and utilization spike — which is also exactly when the epoch is most likely to be stopped and lenders most want to exit.

### Impact Explanation
Broken invariant: liveness of the epoch state machine / "temporary freezing" with fund impact. While `convertToAssets` reverts:

- `stopEpoch` cannot succeed → the epoch stays `isEpochRunning`, `allowAAWithdraw`/`allowBBWithdraw` remain false, so every lender (AA and BB) is locked, including queued withdraw requests waiting for epoch end.
- `startEpoch` cannot succeed either (`_currentVaultAssets()` at line 206), so the stuck state cannot be escaped via a new epoch.
- The freeze persists for as long as the vault-market condition persists (attacker can keep utilization pinned by rolling the borrow), and is only recoverable via the privileged `emergencyExitVault`/`rescueTokens` — which per the threat model are honest roles acting on their own timeline, not a guaranteed rescue.

Quantified impact: 100% of the pool's vault-sleeve TVL (all AA + BB NAV) is non-withdrawable for the duration of the freeze. This is not a gas/DoS-only issue: lender funds and unclaimed yield are concretely frozen, matching the accepted "temporary freezing" impact class.

### Likelihood Explanation
- Trigger conditions (extreme utilization / oracle stress in an underlying vault market) arise naturally in volatile markets — the same regime the Salty report describes — and can also be induced deliberately by an unprivileged user of the vault at the cost of borrow interest.
- No existing guard prevents it: `nonReentrant` doesn't help, the `try/catch` covers only `vault.withdraw`, `skipDefaultCheck`/`Default` paths are unrelated, and `onDefault` (the one hardened path) is not reachable because reaching a default requires `stopEpoch`/`transferFrom` flow to complete.
- Acknowledged-adjacent: the `onDefault` comment proves the team knows `convertToAssets` can be "unavailable during stress", but the mitigation was only applied to the default path, leaving the higher-traffic stop/start path exposed.

### Recommendation
Apply the same defensive pattern used in `onDefault` to the live paths:

1. In `onStopEpoch`, wrap `_currentVaultAssets()` in a `try/catch` (or compute coverage from `vault.balanceOf` + `maxWithdraw`/`previewRedeem` with fallback). If valuation is unavailable, treat the covered shortfall case as "let the later `transferFrom` decide" — i.e., attempt `vault.withdraw` inside the existing `try/catch` and fall back to `return true` rather than reverting on a view failure.
2. Similarly degrade gracefully in `onStartEpoch` (skip `bufferedVaultDelta` carry and reset `bufferStartVaultAssets = 0` when the valuation view is unavailable, mirroring `onDefault`).
3. More generally, consider making `totalInterestDueNow()` resilient (e.g., last-known valuation snapshot) since `IdleCDOEpochVariant` reads it before `onStopEpoch` and a revert there alone is sufficient to brick `stopEpoch`.

### Proof of Concept
Foundry fork concept (mainnet, MetaMorpho-style vault such as `STEAKHOUSE_USDC`, mirroring `test/foundry/ProgrammableBorrowerCreditVault.t.sol` setup):

```solidity
function testConvertToAssetsRevertBricksStopEpoch() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);   cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);          // shares parked in vault, epochAccountingActive = true

    // Attacker: unprivileged user of the vault's underlying markets drives a thin
    // market to ~100% utilization (borrow nearly all supplied liquidity) so that
    // MetaMorpho.totalAssets()/accrueInterest -> convertToAssets reverts.
    _driveMarketToRevertingUtilization();  // attacker EOA, no privileged role

    // Lender queues a withdrawal; epoch reaches its end.
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 1) The valuation view itself reverts:
    vm.expectRevert();
    programmableBorrower.totalInterestDueNow();

    // 2) stopEpoch reverts because IdleCDO reads totalInterestDueNow() and then
    //    onStopEpoch() hits _currentVaultAssets() at lines 245/261 — outside any try/catch.
    vm.prank(owner);
    vm.expectRevert();
    cdoEpoch.stopEpoch();

    // 3) Withdrawals stay disabled: allowAAWithdraw/allowBBWithdraw were never set,
    //    so withdrawAA/withdrawBB revert on _checkWithdrawNotAllowed.
    vm.expectRevert();
    idleCDO.withdrawAA(trancheBal);

    // Only recovery is the honest-privileged escape hatch:
    vm.prank(manager);
    programmableBorrower.emergencyExitVault(0);   // uses vault.redeem, not convertToAssets
    vm.prank(owner);
    cdoEpoch.stopEpoch();                          // now succeeds (shares == 0 -> assets 0)
}
```

Note: the exact revert primitive inside the forked MetaMorpho vault (oracle call failure vs. `accrueInterest`/borrow-rate overflow at pinned utilization) depends on the market composition at fork block; the PoC invariant — an unprivileged vault-market user forcing `convertToAssets` to revert and thereby blocking `stopEpoch` — is what must be demonstrated. A minimal deterministic variant can also be built against the harness's `MockInvariantVault` (`revertConvertToAssets = true`), though for the report the fork version with a real vault is preferable.

Uncertain/unverified: I did not fully confirm the exact call order inside `IdleCDOEpochVariant.stopEpoch` (the claim rests on the in-code comment at `ProgrammableBorrower.sol:256` stating `totalInterestDueNow()` "was already read by IdleCDO before calling this hook" plus the hook itself). If `stopEpoch` tolerates a reverting `totalInterestDueNow`, the finding still stands via `onStopEpoch`'s own unguarded `_currentVaultAssets()` calls at lines 245 and 261.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-211)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-262)
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
    }

    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L289-301)
```text
  function onDefault() external nonReentrant {
    _checkOnlyIdleCDO();
    if (!epochAccountingActive) return;

    bufferedVaultDelta = 0;
    bufferInterest = 0;
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L350-355)
```text
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
