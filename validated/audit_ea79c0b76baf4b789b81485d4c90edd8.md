### Title
Unprivileged ERC4626 vault user can drain liquidity and temporarily freeze `stopEpoch` / withdrawal settlement - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower.onStopEpoch` is the single liquidity gate for `IdleCDOEpochVariant.stopEpoch` in programmable-borrower mode. When IdleCDO needs cash, the borrower contract calls `vault.withdraw(shortfall, ...)` on an external ERC4626 vault (e.g. a MetaMorpho vault). Any unprivileged user of that vault can reduce its available liquidity below the needed `shortfall` — by borrowing against the vault's underlying markets or queueing large withdrawals — causing `vault.withdraw` to revert, which makes `onStopEpoch` revert with `StopEpochVaultLiquidityUnavailable` and the entire `stopEpoch` call to fail. Epoch interest settlement, pending withdraw-request payouts, and the start of the next epoch are all frozen until liquidity returns.

### Finding Description
In `IdleCDOEpochVariant.stopEpoch`, once accounting is resolved the CDO invokes the borrower hook `IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)` (contracts/IdleCDOEpochVariant.sol:395-404). In `ProgrammableBorrower.onStopEpoch` (contracts/strategies/idle/ProgrammableBorrower.sol:231-268):

```solidity
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

`vault.withdraw` reverts whenever the ERC4626 vault cannot source `shortfall` assets — on MetaMorpho-style vaults this happens when market liquidity is borrowed out or allocated to illiquid markets. The revert bubbles up through `onStopEpoch` and reverts the whole `stopEpoch` transaction. There is no fallback path: `emergencyExitVault` uses `vault.redeem` which fails under the same liquidity conditions, and `setVault`/`rescueTokens` are gated on `epochAccountingActive` or unwound positions.

The attacker needs no privilege: they are simply "a user of the programmable borrower's ERC4626 vault" — explicitly an in-scope unprivileged actor. Draining liquidity on a lending-vault requires posting collateral on the underlying markets (cost = borrow interest for the freeze duration), not ownership of the drained funds.

### Impact Explanation
Temporary freezing of user funds. While liquidity is drained:

- `stopEpoch` cannot complete, so `isEpochRunning` stays true, blocking `startEpoch`, normal `requestWithdraw` processing, and deposit flows.
- All matured `claimWithdrawRequest`/`claimInstantWithdrawRequest` payouts are delayed because `collectWithdrawFunds` never runs.
- The freeze persists as long as the attacker keeps vault utilization high; every retry of `stopEpoch` reverts.

Quantified: 100% of pending withdraw requests and the full pool NAV are unclaimable for the duration of the liquidity drain (bounded by how long the attacker pays borrow costs). No permanent loss occurs, matching the "temporary freezing" accepted-impact class rather than insolvency.

### Likelihood Explanation
- Requires only that the configured vault's free liquidity be reducible below `shortfall` — a single borrow/withdraw transaction on the underlying markets at the right moment (e.g. right after `epochEndDate` passes, when a manager will imminently call `stopEpoch`).
- Cost is proportional to the vault's idle liquidity and the freeze duration; on highly utilized vaults a modest borrow suffices.
- No privileged role, no oracle manipulation, no malformed input — pure third-party economic action on a dependency the vault treats as a trusted liquidity source.
- Partially mitigated: the freeze is temporary (retries are explicitly allowed per the code comment "transient ERC4626 liquidity failures can be retried"), and the attacker gains no direct profit, so this is a griefing/extortion vector rather than theft.

### Recommendation
- Add a pull-based failure path: if `vault.withdraw` reverts, consider treating the shortfall as a borrower default (`return false`/`_handleBorrowerDefault`) after a grace period, so a sustained liquidity drain forces recovery instead of an indefinite freeze.
- Track a `stopEpochFailedSince` timestamp: after N failed attempts or a fixed delay, allow a privileged-but-honest caller to route through the default/finalizeDefaultRecovery flow so LPs can exit against whatever liquidity exists.
- Alternatively, keep a configurable portion of funds out of the ERC4626 vault (liquidity buffer sized to `epochPendingWithdraws`) so matured withdrawals can be paid even while vault withdrawal reverts.

### Proof of Concept
```solidity
// Foundry fork test (mainnet, MetaMorpho USDC vault as `vault`)
function testStopEpochFrozenByVaultLiquidityDrain() external {
    // Setup identical to ProgrammableBorrowerCreditVault.t.sol:
    // deposit AA, setIsInterestMinted(true), setIsProgrammableBorrower(true),
    // startEpoch() so funds are parked in the MetaMorpho vault.
    idleCDO.depositAA(10_000 * oneScale);
    _startEpochAndCheckPrices(0);

    // Attacker: unprivileged borrower on Morpho Blue markets underlying the vault.
    // Post collateral and borrow ~all remaining idle liquidity so vault.withdraw reverts.
    address attacker = makeAddr("attacker");
    _drainMorphoVaultLiquidity(attacker); // borrow market liquidity until
                                        // morphoVault.maxWithdraw(programmableBorrower) < shortfall

    // Warp past epoch end; manager attempts to settle the epoch.
    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Epoch never stops: withdrawals stay frozen.
    assertEq(cdoEpoch.isEpochRunning(), true);
    vm.prank(manager);
    vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0); // retry also fails while liquidity is drained

    // Attacker repays the borrow later; only then can stopEpoch succeed.
    _restoreMorphoVaultLiquidity(attacker);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // succeeds — confirming the freeze was the only effect
}
```