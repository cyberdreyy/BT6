### Title
Unprivileged ERC4626 liquidity squeeze permanently/repeatedly reverts `stopEpoch`, freezing all LP funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The Discourse DoS analog: an unprivileged user action (here, borrowing/draining liquidity in the underlying ERC4626 vault markets) makes a core protocol function (`IdleCDOEpochVariant.stopEpoch`) revert indefinitely, freezing every LP's funds for as long as the attacker sustains the liquidity shortage.

### Finding Description
When `isProgrammableBorrower` is set, `_stopEpoch` calls `IProgrammableBorrower(_borrower()).onStopEpoch(...)` directly, without a try/catch, so any revert bubbles up and the whole `stopEpoch`/`stopEpochWithDuration` transaction fails [1](#0-0) .

Inside `ProgrammableBorrower.onStopEpoch`, when on-hand cash is insufficient the code first checks whether the vault position economically covers the shortfall (`shortfall > _currentVaultAssets()` → return `true`, letting the default path run). But if shares cover the shortfall while the vault's *withdrawable liquidity* does not, `vault.withdraw` reverts and the hook reverts with `StopEpochVaultLiquidityUnavailable` [2](#0-1) .

For ERC4626 vaults like MetaMorpho, `withdraw` reverts when underlying market liquidity is fully borrowed. Any unprivileged market participant can borrow all available liquidity (a normal, permissionless operation). While that state holds:

- `stopEpoch` always reverts, so `epochEndDate` has passed but `isEpochRunning` stays true and `epochNumber` never increments.
- All pending withdraw receipts stay unfunded (`collectWithdrawFunds` is never reached) and all active LP principal is locked in the vault sleeve.
- `emergencyExitVault` uses `vault.redeem`, which is blocked by the same liquidity shortage, so there is no privileged escape [3](#0-2) .
- The attacker can re-borrow any repaid liquidity to extend the freeze arbitrarily; the only cost is the borrow APR on the drained amount.

### Impact Explanation
Temporary-to-permanent freezing of 100% of pool TVL: every pending withdraw receipt and every active tranche position is frozen for the duration of the liquidity squeeze. Quantified loss = full pool NAV × freeze duration (missed yield plus forced holding of a matured credit position), and it can be extended indefinitely at borrow-interest cost. Unlike the insolvency branch (which correctly falls through to `transferFrom` failure and `_handleBorrowerDefault`), this branch produces a plain revert with no state transition, so the default/finalization machinery can never be engaged to settle losses — LPs cannot even exit at a haircut.

### Likelihood Explanation
Requires a programmable-borrower deployment parked in a shared-liquidity ERC4626 vault (the production configuration the tests fork against). The attacker needs capital to borrow the vault's idle market liquidity; on under-collateralized-but-deep markets this is bounded by available liquidity, not pool size. No privileged role, timing race, or borrower misbehavior is needed — the revert path is deterministic once `withdraw` would fail.

### Recommendation
Treat a liquidity-failed vault withdrawal like a default rather than a revert: in `onStopEpoch`, return `false` (or a distinct status) when `vault.withdraw` reverts so `IdleCDOEpochVariant` can route to `_handleBorrowerDefault` / recovery accounting instead of bricking `stopEpoch`. Alternatively, expose a permissionless `triggerDefault` when `block.timestamp > epochEndDate` and the hook has failed N times, and make `emergencyExitVault` able to mark the position defaulted without redeeming.

### Proof of Concept
Foundry fork PoC sketch (mainnet, using the MetaMorpho vault already used in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testStopEpochFrozenByVaultLiquiditySqueeze() external {
    // Setup identical to testProgrammableBorrowerStopEpochAutoRealizesInterestWithRealVault:
    // isInterestMinted = true, depositAA(10_000e6), startEpoch, funds parked in morphoVault.
    vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(10_000 * oneScale);
    _startEpochAndCheckPrices(0);

    // Attacker (any EOA): drain withdrawable liquidity from the Morpho markets
    // that morphoVault allocates to, e.g. borrow max on each listed market.
    // After this, morphoVault.maxWithdraw(programmableBorrower) == 0 while
    // convertToAssets(shares) still covers the shortfall.
    _drainMorphoLiquidity(attacker);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Epoch stays running, no default recorded, nothing claimable.
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());

    // Repeat ad infinitum: attacker re-borrows repaid liquidity; stopEpoch keeps reverting.
}
```

The key assertion is that `vault.withdraw` reverts *while* `shortfall <= _currentVaultAssets()`, which is precisely the revert branch — the insolvency branch (`shortfall > _currentVaultAssets()`) is intentionally handled and is not the bug.

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
