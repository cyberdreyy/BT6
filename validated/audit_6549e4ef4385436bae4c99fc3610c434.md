### Title
Attacker can permanently block `stopEpoch` by draining ERC4626 vault liquidity, freezing all credit-vault LP funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external bug is an unhandled error propagating to the top and taking the service down. The on-chain analog in `ProgrammableBorrower.onStopEpoch`: when the programmable borrower's ERC4626 vault (e.g., a MetaMorpho vault) is asset-rich but liquidity-poor, `vault.withdraw` reverts and the hook deliberately rethrows `StopEpochVaultLiquidityUnavailable`. That revert bubbles through `IdleCDOEpochVariant._stopEpoch` before any default handling, so the epoch can neither stop nor enter the borrower-default path. Any unprivileged user of the underlying ERC4626 vault can create this state by removing idle liquidity, freezing every LP's principal and pending withdrawals.

### Finding Description
`stopEpoch` calls `onStopEpoch` directly and lets reverts bubble: [1](#0-0) 

Inside `onStopEpoch`, when on-hand cash is below `_amountRequired`, the borrower tries to withdraw the shortfall. If the vault's *share valuation* covers the shortfall but its *idle liquidity* does not, `vault.withdraw` reverts and the catch rethrows: [2](#0-1) 

The two default paths are unreachable in this state: `onStopEpoch` only returns `false` for close-pool-with-debt, and the `try this.getFundsFromBorrower` catch at `IdleCDOEpochVariant.sol:501-505` is never reached because the hook reverts first. `_handleBorrowerDefault` is internal, and `finalizeDefault` requires `defaulted == true`. `emergencyExitVault` is owner/manager-only, so no permissionless escape exists. For an ERC4626 vault like MetaMorpho, an attacker with collateral can borrow the vault's underlying-market liquidity (or, if the vault holds idle assets, any user pulling them out) so that `convertToAssets` still covers the shortfall but `withdraw` reverts.

### Impact Explanation
While the vault is illiquid, every `stopEpoch` call reverts: `isEpochRunning` stays true, `epochNumber` never increments, `claimWithdrawRequest` keeps reverting (`epochNumber <= lastWithdrawRequest`), and `claimInstantWithdrawRequest` is gated. All LP principal, accrued yield, and pending withdrawal receipts are frozen in the programmable borrower. The attack is repeatable: the attacker can restore liquidity, wait for a retry window, and re-drain, extending the freeze indefinitely. Loss is quantifiable as the full pool NAV (e.g., 100% of deposited underlying plus matured withdraw claims) made illiquid for the attack duration.

### Likelihood Explanation
Requires the programmable-borrower mode (`isProgrammableBorrower == true`), an epoch whose `stopEpoch` needs to pull more than the borrower's on-hand cash (true whenever funds are parked in the vault via `onStartEpoch`'s `_depositToVault`, which is the normal configuration), and an attacker able to take the vault's idle liquidity — a routine borrow against MetaMorpho markets or withdrawing idle assets. Cost is only collateral plus gas; no privileged role needed.

### Recommendation
Catch the hook failure in `_stopEpoch` instead of letting it bubble — e.g., `try IProgrammableBorrower(...).onStopEpoch(...)` and route failures into `_handleBorrowerDefault` (or a bounded-retry state), so a liquidity-starved vault degrades to the existing default/recovery path rather than bricking the epoch machine. Alternatively, make `onStopEpoch` revert only on genuinely transient errors and return `false` when `vault.withdraw` fails despite covered shares.

### Proof of Concept
Foundry fork test (mainnet fork, MetaMorpho vault as configured in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testVaultLiquidityDrainBlocksStopEpoch() external {
  uint256 amount = 10_000 * oneScale;
  vm.prank(owner);
  cdoEpoch.setIsInterestMinted(true);
  idleCDO.depositAA(amount);
  _startEpochAndCheckPrices(0);          // onStartEpoch deposits all cash into the vault

  // Attacker (any ERC4626 vault user) removes idle liquidity from the MetaMorpho
  // vault's underlying markets so withdraw() reverts while convertToAssets still covers.
  _drainMorphoVaultLiquidity();          // borrow/withdraw idle market liquidity on fork

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
  cdoEpoch.stopEpoch(0, 0);              // always reverts while vault is illiquid

  assertFalse(cdoEpoch.defaulted());     // default path unreachable
  assertTrue(cdoEpoch.isEpochRunning());
  // LP claim stays frozen:
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.claimWithdrawRequest();
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
