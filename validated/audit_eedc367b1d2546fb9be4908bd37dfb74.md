### Title
Programmable-borrower vault liquidity griefing blocks `stopEpoch` and freezes matured withdraw requests - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external bug class — an unprivileged party injecting attacker-controlled state into a privileged execution path (`.sfw.config` → env vars) — maps onto `IdleCDOEpochVariant._stopEpoch`, where the epoch's closing settlement is delegated to externally observable ERC4626 vault state that any vault user can manipulate. `ProgrammableBorrower.onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable` whenever `vault.withdraw(shortfall)` fails while the vault *nominally* covers the shortfall. Because IdleCDO deliberately lets that revert bubble, any ERC4626 vault user (an allowed attacker role) who keeps the shared vault illiquid across the epoch boundary can repeatedly revert `stopEpoch`, freezing all pending withdraw-request payouts and locking LP principal for as long as the illiquidity is maintained.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch` the programmable-borrower hook is invoked directly, not inside a try/catch: if `IProgrammableBorrower(_borrower()).onStopEpoch(...)` reverts, the whole `stopEpoch` reverts — this is intentional ("Hook reverts bubble so transient ERC4626 liquidity failures can be retried") [1](#0-0) .

Inside `ProgrammableBorrower.onStopEpoch`, when the required cash exceeds on-hand balance, the contract checks `shortfall > _currentVaultAssets()` and returns `true` (deferring to the transferFrom/default path) only if the vault *cannot economically cover* the shortfall. In every other case it calls `vault.withdraw(shortfall, ...)` inside a try/catch and reverts `StopEpochVaultLiquidityUnavailable` on failure [2](#0-1) .

`_currentVaultAssets()` is a `convertToAssets` valuation [3](#0-2) , which for lending-style ERC4626 vaults (e.g. Morpho-type markets, the vault used in the repo's own fork tests) reports full share value even when *withdrawable liquidity* is borrowed out. So the guard passes, `vault.withdraw` reverts, and `stopEpoch` reverts.

An unprivileged vault user exploits this by sequencing around the honest manager's `stopEpoch` call:

1. Epoch is running in programmable + minted-interest mode; LPs have pending withdraw requests maturing at epoch end (`_pendingWithdraws > 0`).
2. At `epochEndDate`, the attacker borrows/withdraws available liquidity from the shared ERC4626 vault (legitimate vault usage), leaving less free liquidity than the CDO's shortfall while `convertToAssets` still values the borrower contract's shares above it.
3. Every `stopEpoch`/`stopEpochWithDuration` call reverts in `onStopEpoch` → `StopEpochVaultLiquidityUnavailable`.
4. The epoch cannot be stopped: `isEpochRunning` stays true, `epochEndDate` stays in the past, `allowInstantWithdraw` paths cannot settle, and `claimWithdrawRequest`/`collectWithdrawFunds` never receive funds. Attacker maintains the freeze for as long as they keep liquidity drained (renewable each block via borrow).

The broken invariant is epoch-liveness: "a solvent borrower facility can always be stopped at `epochEndDate`". The existing guards do not stop it: `_checkNotAllowed` conditions at `_stopEpoch` entry are all satisfied, `shortfall > _currentVaultAssets()` is false under price-vs-liquidity divergence, and the `try/catch` converts the failure into a hard revert rather than letting IdleCDO proceed to the partial-pull/default path.

### Impact Explanation
Temporary freezing of LP funds: all matured withdraw-request claims (`withdrawsRequestsByEpoch` receipts) and the entire active NAV remain locked in the facility for the duration of the attacker-maintained vault illiquidity. Quantified loss equals `_pendingWithdraws + getContractValue()` frozen for the illiquidity window; on a lending vault the attacker can sustain this by rolling a borrow position, and the freeze also blocks `startEpoch`, depositor exits via `requestWithdraw` interest accrual correctness, and any emergency close-pool (`_interest == 1`) attempt routed through the same hook.

### Likelihood Explanation
Requires a programmable-borrower deployment whose ERC4626 vault permits third-party liquidity-taking (standard for Morpho/AAVE-style vaults — the repo's own tests integrate a Morpho vault, see `testEmergencyExitVaultFullRedeem` using `morphoVault`). The attacker needs capital sufficient to drain vault liquidity below the stop-epoch shortfall, which for a large borrowed position is a routine leveraged borrow, not an exotic action. No privileged role is needed — the attacker is simply a user of the programmable borrower's ERC4626 vault, an explicitly in-scope unprivileged actor. The attack is detectable and griefing-class (attacker pays borrow interest), which limits motive to extortion/griefing rather than direct profit, but cost is bounded by borrow interest while frozen TVL is unbounded.

### Recommendation
In `ProgrammableBorrower.onStopEpoch`, do not let a *covered-but-illiquid* withdrawal failure hard-revert the whole stop. Options:
- On `vault.withdraw` catch, attempt `vault.maxWithdraw(address(this))` and pull the partial amount, returning `true` so IdleCDO's `transferFrom` pulls what exists and routes the genuine shortfall into `_handleBorrowerDefault` rather than a revert.
- Or split the hook into a non-reverting "recall as much as possible" plus letting the subsequent `getFundsFromBorrower` shortfall trigger the existing default accounting, so transient illiquidity settles claims pro-rata instead of freezing them.

### Proof of Concept
Foundry fork test sketch (realvault = Morpho-style ERC4626 on mainnet, mirroring `ProgrammableBorrowerCreditVault.t.sol` setup):

```solidity
function testVaultIlliquidityFreezesStopEpoch() external {
    // 1. LPs deposit, manager starts epoch in programmable mode
    idleCDO.depositAA(1_000_000e6);
    _startEpochAndCheckPrices(0);

    // 2. LP requests withdraw so pendingWithdraws > 0
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    uint256 pending = strategy.pendingWithdraws();
    assertGt(pending, 0);

    // 3. Attacker (unprivileged vault user) borrows vault liquidity
    //    so maxWithdraw(programmableBorrower) < pending shortfall
    //    while convertToAssets still covers it.
    uint256 liq = underlying.balanceOf(address(morphoVault));
    deal(collateral, attacker, type(uint256).max);
    _morphoBorrowAll(attacker, liq - 1); // leave < shortfall withdrawable

    // 4. Warp past epoch end; honest manager stopEpoch reverts
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // 5. Retry also reverts — epoch stuck, withdraw receipts unpayable
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.isEpochRunning());
    vm.expectRevert(); // nothing to claim
    cdoEpoch.claimWithdrawRequest();
}
```

The same revert path is reachable with a mock vault whose `convertToAssets` exceeds `maxWithdraw` (e.g. `vault.setWithdrawLimit(x)` as already used in `ProgrammableBorrowerAccountingInvariant.t.sol`), making the PoC reproducible without relying on a specific lending market's borrow liquidity.

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```
