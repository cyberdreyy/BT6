### Title
ProgrammableBorrower.sol - If the ERC4626 vault goes inactive, `stopEpoch` permanently bricks and LP withdrawals freeze - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
Analogous to Olympus TRSRY `active == false` bricking `Clearinghouse.rebalance()`, the programmable-borrower mode of `IdleCDOEpochVariant` hard-depends on a single external ERC4626 `vault`. If that vault becomes inactive such that `convertToAssets`/`withdraw`/`redeem` revert, `IdleCDOEpochVariant._stopEpoch` reverts forever: `epochNumber` never advances, `isEpochRunning` stays true, and every pending withdraw request and all LP principal in the credit vault are permanently frozen.

### Finding Description
In programmable-borrower mode, `_stopEpoch` reads the epoch interest via `_resolveStopEpochInterest`, which calls `IProgrammableBorrower.totalInterestDueNow()` with no try/catch. That view flows into `_vaultNetInterest` → `_currentVaultAssets`, which calls `vault.convertToAssets(shares)` — a live call into the third-party vault. [1](#0-0) [2](#0-1) 

The same unguarded valuation is read again inside `onStopEpoch` when deciding whether a shortfall is economically covered (`shortfall > _currentVaultAssets()`), and a covered-but-failing `vault.withdraw` is deliberately re-thrown as `StopEpochVaultLiquidityUnavailable`, bubbling out of `stopEpoch` uncaught. [3](#0-2) [4](#0-3) 

Because the revert happens before `collectWithdrawFunds`/`_updateAccounting`/`epochNumber` increment, `IdleCreditVault.claimWithdrawRequest` keeps reverting on the stale-epoch check (`epochNumber <= lastWithdrawRequest[_user]`), so pending receipts can never be claimed. [5](#0-4) 

The escape hatches do not cover the "vault gone inactive" case:
- `setVault` requires `!epochAccountingActive && vault.balanceOf(this) == 0`, both of which stay true only after a successful stop — circular dependency. [6](#0-5) 
- `emergencyExitVault` calls `vault.redeem`, which reverts on a vault whose withdrawals are disabled. [7](#0-6) 
- `onDefault` was deliberately written to avoid `convertToAssets`, confirming the authors know the vault's valuation can go down — but the *pre-default* path (`totalInterestDueNow`, `onStopEpoch`) still requires it, so the default path is unreachable. [8](#0-7) 

The same pattern exists on the start side: `onStartEpoch` → `_depositToVault` → `vault.deposit` is uncaught, so a vault with deposits disabled also bricks `startEpoch`. [9](#0-8) [10](#0-9) 

### Impact Explanation
Permanent freezing of funds. All LP principal parked in the vault sleeve plus all pending withdraw receipts (`pendingWithdraws`) are stuck: `stopEpoch` reverts, the epoch state machine never advances, `claimWithdrawRequest`/`claimInstantWithdrawRequest` revert on the epoch check, and no owner/manager action (`setVault`, `emergencyExitVault`, default path) can recover because every one of them either requires the epoch to be settled or calls a reverting vault function. Total loss equals the full pool TVL plus pending claims — identical in shape to the reported `rebalance` brick where `withdrawReserves` reverting halts the protocol.

### Likelihood Explanation
Low, mirroring the source report. It requires the configured ERC4626 vault (e.g., a Morpho/Gauntlet vault) to enter a state where `withdraw`/`redeem`/`convertToAssets` revert — pause, market freeze, or a reverting valuation path. The codebase itself treats transient liquidity failure as plausible (`StopEpochVaultLiquidityUnavailable` exists precisely for retryable recall failures), but assumes such failures are temporary; a permanent inactive state has no recovery path. Medium severity.

### Recommendation
Wrap the external-vault interactions in `onStopEpoch` in a way that lets the epoch settle even when the vault is dead:
- In `onStopEpoch`, wrap `_currentVaultAssets()` and `vault.withdraw` in try/catch; on a persistent valuation/withdrawal failure, return `false` (or a new status) so `IdleCDOEpochVariant` routes into `_handleBorrowerDefault`/`_emergencyShutdown` rather than reverting, letting losses be socialized through the existing BB-first waterfall instead of freezing.
- Add a privileged "force default / abandon vault sleeve" path (e.g., `abandonVault()`) callable while `epochAccountingActive` that zeroes the vault position in accounting without touching the vault, so `setVault`/`onDefault` become reachable.
- Similarly, consider making `_depositToVault` failure inside `onStartEpoch` non-fatal (keep funds on hand) so `startEpoch` cannot be bricked by a deposit-disabled vault.

### Proof of Concept
Foundry PoC sketch (modeled on `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which already ships `MockStopEpochLiquidityVault`):

```solidity
// test/foundry/PoCVaultInactive.t.sol
function testStopEpochBrickedWhenVaultInactive() external {
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);   // funds parked in the ERC4626 vault

    // Vault goes inactive: every mutating + valuation call reverts
    MockStopEpochLiquidityVault dead = MockStopEpochLiquidityVault(address(programmableBorrower.vault()));
    vm.mockCallRevert(address(dead), abi.encodeWithSelector(IERC4626.convertToAssets.selector), "");
    vm.mockCallRevert(address(dead), abi.encodeWithSelector(IERC4626.withdraw.selector), "");
    vm.mockCallRevert(address(dead), abi.encodeWithSelector(IERC4626.redeem.selector), "");

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 1) stopEpoch permanently reverts (uncaught convertToAssets / StopEpochVaultLiquidityUnavailable)
    vm.prank(manager);
    vm.expectRevert();
    cdoEpoch.stopEpoch(0, 0);

    // 2) retry still fails -> epoch stuck running, epochNumber never increments
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());

    // 3) pending withdraw receipt can never be claimed (stale-epoch check)
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // 4) privileged escapes are also bricked
    vm.prank(owner);
    vm.expectRevert();                                  // vault.redeem reverts
    programmableBorrower.emergencyExitVault(0);
    vm.prank(owner);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector)); // epochAccountingActive + shares != 0
    programmableBorrower.setVault(address(0xBEEF));
}
```

Note: the exact mock mechanics depend on the test harness's mock vault (a `setInactive()`-style flag on `MockStopEpochLiquidityVault` achieves the same). Uncertain detail: whether `totalInterestDueNow` is also invoked earlier in the same `stopEpoch` call path — it is, via `_resolveStopEpochInterest` before `onStopEpoch`, so reverting `convertToAssets` alone is sufficient to brick the function even before the withdraw attempt.

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

**File:** contracts/IdleCDOEpochVariant.sol (L1001-1005)
```text
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L163-169)
```text
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L216-216)
```text
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L245-253)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L295-297)
```text
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-371)
```text
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
      epochWithdrawnFromVault += assets;
    }
    emit RedeemedFromVault(_shares, assets, address(this));
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```
