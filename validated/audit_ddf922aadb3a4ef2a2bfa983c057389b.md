### Title
Permanent freeze of all tranche funds when the programmable borrower's ERC4626 vault breaks — no partial withdrawal path (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
Analogous to Perennial's `BalancedVault` breaking when one underlying market fails, `IdleCDOEpochVariant` + `ProgrammableBorrower` concentrate all pool liquidity into a single external ERC4626 vault (`vault`). If that vault permanently fails (e.g. `convertToAssets` or `withdraw`/`redeem` start reverting — a broken oracle inside MetaMorpho-style vaults, a frozen market, or a bricked adapter), `stopEpoch` can never complete and every tranche holder's funds (both AA and BB, including on-hand cash not even deposited in the vault) are permanently locked. There is no partial/emergency withdrawal path for users.

### Finding Description
`ProgrammableBorrower` deposits all pool assets into an external ERC4626 vault at `onStartEpoch` via `_depositToVault` (`ProgrammableBorrower.sol:216,378-385`). The epoch lifecycle hard-depends on that vault in two places:

1. `stopEpoch` → `_resolveStopEpochInterest` calls `IProgrammableBorrower.totalInterestDueNow()` (`IdleCDOEpochVariant.sol:1001-1005`), which calls `_vaultNetInterest` → `_currentVaultAssets` → `vault.convertToAssets(shares)` (`ProgrammableBorrower.sol:330-348,546-549`). If the vault's valuation reverts, `stopEpoch` reverts unconditionally — there is no try/catch around this call.
2. `onStopEpoch` calls `vault.withdraw(shortfall, ...)` inside a try/catch, but on failure it reverts with `StopEpochVaultLiquidityUnavailable()` (`ProgrammableBorrower.sol:246-253`). The comment says this is meant for *transient* liquidity failures that "can be retried" — a permanent vault failure makes every `stopEpoch` retry revert forever.

The design already acknowledges the broken-valuation risk asymmetrically: `onDefault` deliberately avoids calling `convertToAssets` with the comment "Even if the external vault's valuation view is unavailable during stress, CDO default handling must still be able to shut down borrowing" (`ProgrammableBorrower.sol:295-296`). But that escape only exists on the borrower-default path; there is no equivalent for the vault-failure path, and default handling can't even be reached because `stopEpoch` reverts before `_handleBorrowerDefault` runs (default is only triggered inside `stopEpoch`'s flow or the `getFundsFromBorrower` try at `IdleCDOEpochVariant.sol:408`).

Broken invariant: continuous epoch progression / user exit. While the epoch can't stop:
- `paused()` stays true and `isEpochRunning` stays true, so `requestWithdraw` is blocked (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` were cleared at `startEpoch`) and `_beforeUnpause` blocks external unpause (`IdleCDOEpochVariant.sol:635-637`).
- `claimWithdrawRequest`/`claimInstantWithdrawRequest` need funds that only arrive via `getFundsFromBorrower` in `stopEpoch`.
- Even idle cash sitting in `ProgrammableBorrower` (never deposited to the vault) is unreachable, because `stopEpoch` reverts before `getFundsFromBorrower` can pull it. This is the direct analog of Alice losing ETH/USD funds because ARB/USD broke.
- `emergencyExitVault` (`ProgrammableBorrower.sol:361-372`) also calls `vault.redeem` and reverts. `rescueTokens` can only move the vault *shares*, not recover underlying.

Unlike Perennial, there isn't even a `_maxRedeemAtEpoch`-style partial redeem that could be argued away; the freeze is total, including funds never at risk in the vault.

### Impact Explanation
Permanent freezing of 100% of lender funds (AA + BB tranches plus pending withdraw requests) in a programmable-borrower credit vault, triggered by a single external dependency failure — the exact risk class the Perennial report describes (one broken market kills the whole vault, no way to cut losses). Impact is total loss of access to funds, not just yield.

### Likelihood Explanation
Requires the underlying ERC4626 vault to permanently break or permanently revert on valuation/withdrawal. This is tail-risk (same severity rationale as Perennial M-16, kept as Medium there), but the vault is a permissionless external integration and the code itself contemplates valuation failure "during stress" in `onDefault`, so it is a scenario the authors deemed realistic — just not handled on the stop path.

### Recommendation
Give `stopEpoch` a fallback when the external vault is unhealthy: e.g. wrap `totalInterestDueNow()` and the `vault.withdraw` in a recoverable path that (a) pulls whatever on-hand cash exists, (b) accounts the vault sleeve as a loss so the epoch can close and users can exit pro-rata, or (c) routes through an owner-triggered "vault-default" mode that settles requests against on-hand funds and transfers vault-share claims separately. At minimum, document that failure of the ERC4626 vault permanently freezes all pool funds.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// setup: credit vault with isProgrammableBorrower, deposits parked in morphoVault
idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);              // onStartEpoch deposits all into vault
// vault breaks: valuation and withdrawals revert permanently
vault.setRevertConvertToAssets(true);      // or setWithdrawLimit(0) + revert on withdraw
// borrower is honest and ready to repay; manager tries to stop
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert();                         // reverts inside totalInterestDueNow/convertToAssets
cdoEpoch.stopEpoch(0, 0);
// every retry reverts; epoch never stops
vm.prank(manager);
vm.expectRevert();
cdoEpoch.stopEpoch(0, 0);
// users cannot request or claim withdrawals; paused stays true
assertTrue(cdoEpoch.isEpochRunning());
vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
cdoEpoch.requestWithdraw(0, address(AAtranche));
// even on-hand cash in ProgrammableBorrower is unreachable
assertGt(underlying.balanceOf(address(programmableBorrower)), 0);
```

To model only `withdraw` reverting (valuation fine), use the existing `vault.setWithdrawLimit`/revert hook from `ProgrammableBorrowerCreditVault.t.sol` / `ProgrammableBorrowerAccountingInvariant.t.sol` and observe `stopEpoch` revert with `StopEpochVaultLiquidityUnavailable` forever, while `underlyingToken.balanceOf(programmableBorrower)` remains stranded.

Cited code: `onStopEpoch` revert path [1](#0-0) , vault valuation [2](#0-1) , interest read at stop [3](#0-2) , unpause guard [4](#0-3) , default path intentionally avoiding valuation [5](#0-4) .

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L246-253)
```text
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L293-300)
```text
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L634-637)
```text
  /// @notice Prevent external unpause while an epoch, emergency shutdown, or hard default is active.
  function _beforeUnpause() internal view override {
    _checkNotAllowed(defaulted || isEpochRunning || skipDefaultCheck);
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
