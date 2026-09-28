### Title
ProgrammableBorrower stop-epoch settlement can be indefinitely griefed by draining ERC4626 vault liquidity - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The analog of the BigBlueButton fix — resolving an external resource without pinning/restricting what it resolves to — is `ProgrammableBorrower.onStopEpoch` trusting the live liquidity state of an external ERC4626 vault. Any unprivileged user of that vault (explicitly in-scope) can drain its idle liquidity at the epoch boundary so `vault.withdraw` reverts, causing `StopEpochVaultLiquidityUnavailable` to bubble up and revert `stopEpoch` entirely.

### Finding Description
In `stopEpoch`, `IdleCDOEpochVariant` calls the programmable-borrower hook and lets reverts propagate: [1](#0-0) 

`onStopEpoch` withdraws the shortfall from the configured ERC4626 vault and reverts on any failure: [2](#0-1) 

The only guard is `shortfall > _currentVaultAssets()` → return `true` (default path). That check covers *insufficient shares value*, but not the case where shares have full value yet the vault has no idle underlying (all lent out on Morpho markets, or a `maxWithdraw`/`withdrawLimit` below the shortfall). `vault.withdraw` then reverts, `StopEpochVaultLiquidityUnavailable` propagates, and the whole `stopEpoch` reverts — no default, no partial settlement. Tests confirm revert-bubbling is intentional ("Hook reverts bubble so transient ERC4626 liquidity failures can be retried"), so there is no fallback path.

Because the vault (e.g. MetaMorpho) is a public market, an unprivileged attacker can:
1. Deposit/borrow through the same ERC4626 vault or its underlying Morpho markets to leave `vault.maxWithdraw(programmableBorrower) < shortfall`.
2. Let `manager.stopEpoch()` revert.
3. Re-borrow or sandwich each retry to keep liquidity below the shortfall indefinitely (borrow cost = market interest only, and the attacker can even lend into the same market to recoup it).

During the freeze: `isEpochRunning` stays `true`, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` stay `false` [3](#0-2) , pending receipts cannot be claimed, and deposits stay paused.

### Impact Explanation
Temporary freezing of all pool funds: withdraw-request holders cannot be paid, new requests are disabled, and the epoch cannot settle while the attacker maintains the liquidity drain. With a MetaMorpho-style vault the attacker can repeat the borrow cheaply every block, extending the freeze arbitrarily; unclaimed yield and pending payouts stay locked for the duration.

### Likelihood Explanation
Requires the programmable-borrower deployment mode with a public ERC4626 vault, and the shortfall must exceed on-hand cash but be below `convertToAssets` of shares (otherwise the default path absorbs it). That condition is typical at epoch end when most principal is parked in the vault. Cost to the attacker is limited to market borrow interest for the griefing window, making a sustained attack feasible.

### Recommendation
Inside `onStopEpoch`, pre-check `vault.maxWithdraw(address(this))` (or catch the revert and degrade gracefully): if withdrawable liquidity is insufficient, either withdraw only what is available and return `false`/`true` deterministically so `stopEpoch` can route to `_handleBorrowerDefault` or a partial-settlement path instead of reverting. Alternatively, add a bounded retry/timeout after which the CDO treats the facility as defaulted, preventing unbounded freezing by external vault liquidity state.

### Proof of Concept
Foundry fork (mainnet, MetaMorpho USDC vault, based on `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testStopEpochGriefedByVaultLiquidityDrain() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);            // funds parked in morphoVault

    // Attacker: unprivileged user drains idle liquidity from the underlying
    // Morpho markets so morphoVault.maxWithdraw(programmableBorrower) < shortfall.
    // (borrow all available loan token via attacker-supplied collateral,
    //  or redeem idle liquidity into a market with zero supply)
    _drainMorphoLiquidity(); // leaves maxWithdraw < pendingWithdraws shortfall

    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Epoch still running; withdraw requests stay disabled; no default declared.
    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.allowAAWithdrawRequest());
    assertFalse(cdoEpoch.defaulted());
}
```

Note: validity hinges on `stopEpoch` having no alternate settlement path when the hook reverts — the intentional "revert and retry" design converts a transient external-vault state into a repeatable freeze, which is why `maxWithdraw` awareness is needed.

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

**File:** contracts/IdleCDOEpochVariant.sol (L478-483)
```text
      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
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
