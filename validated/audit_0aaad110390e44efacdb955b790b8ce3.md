I found the programmable-borrower hooks, which are the closest analog surface (external hook reverts bubbling into the epoch flow). Let me check `startEpoch`'s `onStartEpoch` call and the withdraw/claim path to see if an unprivileged vault user can permanently block epoch transitions.### Title
`ProgrammableBorrower.onStopEpoch` vault-withdraw revert bubbles up and freezes `stopEpoch`, blocking all LP withdraw claims - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
Analogous to the rewarder report — where a child `onReward()` revert propagates into the parent and freezes `withdraw()` — `IdleCDOEpochVariant._stopEpoch` calls the external hook `IProgrammableBorrower.onStopEpoch` **outside** any `try/catch`. When the hook's inner `vault.withdraw` fails while `convertToAssets` claims coverage, the hook reverts `StopEpochVaultLiquidityUnavailable`, so the entire `stopEpoch` reverts, `isEpochRunning` stays `true`, and every pending withdraw request in `IdleCreditVault` remains unclaimable. An unprivileged user of the programmable borrower's ERC4626 vault (e.g. draining its idle liquidity) can sustain this indefinitely.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol:395-404`, `_stopEpoch` invokes the borrower hook directly:

```solidity
if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
    _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    return;
}
```

The comment at line 396-397 states "Hook reverts bubble so transient ERC4626 liquidity failures can be retried." Inside `ProgrammableBorrower.onStopEpoch` (`contracts/strategies/idle/ProgrammableBorrower.sol:239-254`), coverage is checked against `convertToAssets`-based `_currentVaultAssets()`, not against `maxWithdraw` / actual withdrawable liquidity:

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

For a lending-style ERC4626 vault, `convertToAssets` includes lent-out assets, so `convertToAssets(shares) > maxWithdraw` whenever markets are illiquid. An attacker who borrows from the vault's markets (a permitted actor class: "a user of the programmable borrower's ERC4626 vault") makes `vault.withdraw` revert while `shortfall <= _currentVaultAssets()`. The hook then reverts, and since the call sits before the `try this.getFundsFromBorrower` block, the revert unwinds all of `_stopEpoch` — no default is triggered, `isEpochRunning` remains `true`, `allowAAWithdrawRequest`/`allowBBWithdrawRequest` stay `false`, and `pendingWithdraws` are never funded, so `claimWithdrawRequest` in `IdleCreditVault` has no escrowed funds to pay out.

The asymmetry mirrors the rewarder bug exactly: genuine insolvency (`shortfall > _currentVaultAssets`) is handled gracefully via `return true` → failed `transferFrom` → `_handleBorrowerDefault`, but a *failure of the subsidiary call itself* reverts the parent flow rather than degrading to the default path.

### Impact Explanation
Temporary freezing of funds: while the epoch cannot be stopped, all pending withdraw requests (potentially the full pool NAV if LPs requested) cannot be claimed. The freeze persists for as long as the attacker keeps vault liquidity below `_amountToPullFromBorrower + pendingWithdraws`; the attacker can re-drain liquidity each time the market refills, at only the cost of borrow interest. Neither `stopEpoch` nor `stopEpochWithDuration` can complete, and because a revert — not a `false` return — occurs, the sanctioned `_handleBorrowerDefault` escape is unreachable. The existing guards (the `return true` coverage check, the `try/catch` on `getFundsFromBorrower`) only cover under-coverage and transfer failures, not hook reverts.

### Likelihood Explanation
Requires a programmable-borrower deployment and a vault whose withdrawable liquidity is below share value — a normal state for lending-market vaults, deliberately inducible by any unprivileged borrower of those markets. Only the timing (keeping liquidity drained until the manager is forced to default via other means or liquidity returns) bounds the freeze. No privileged cooperation needed.

### Recommendation
Treat hook reverts as a stop-path failure rather than propagating them: wrap `IProgrammableBorrower(...).onStopEpoch` in a `try/catch` inside `_stopEpoch`, and on catch either (a) retry-fail cleanly while still allowing an explicit `forceDefault`/`stopEpoch` fallback, or (b) route into `_handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws)` after a configurable grace period. Alternatively, in `ProgrammableBorrower.onStopEpoch`, base the coverage check on `vault.maxWithdraw(address(this))` and return a distinct status instead of reverting, so the CDO can distinguish "transient illiquidity" from "permanent default" and LPs are not frozen at the attacker's discretion.

### Proof of Concept
Foundry fork/test outline (mirrors `test/foundry/ProgrammableBorrowerCreditVault.t.sol:372-399`, which already proves the revert path — extend it to a *sustained* freeze):

```solidity
function test_AttackerFreezesStopEpochViaVaultIlliquidity() external {
    // Setup: programmable borrower with a lending-style vault where
    // convertToAssets > maxWithdraw when markets are drained.
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);  cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    // Attacker: unprivileged user of the ERC4626 vault borrows available
    // liquidity such that 0 < maxWithdraw(programmableBorrower) < shortfall,
    // while convertToAssets still covers it.
    vault.setWithdrawLimit(0); // models drained market liquidity
    // invariant: convertToAssets(shares) >= pendingWithdraws still holds
    assertGe(vault.convertToAssets(vault.balanceOf(address(programmableBorrower))),
             strategy.pendingWithdraws());

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Every stop attempt reverts; default path is never reached.
    for (uint i; i < 5; ++i) {
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);
        vm.warp(block.timestamp + 1 days); // attacker re-drains any refill
    }

    assertFalse(cdoEpoch.defaulted());
    assertTrue(cdoEpoch.isEpochRunning());
    assertGt(strategy.pendingWithdraws(), 0);
    // LP claim reverts / pays 0: escrowed withdraw funds were never collected.
    vm.expectRevert(); // or assertEq payout == 0 depending on claim impl
    strategy.claimWithdrawRequest(/* receipt */);
}
```

Caveat: the repository's own tests acknowledge this revert as an intentional "retryable" path (`ProgrammableBorrowerCreditVault.t.sol:372`), so a defender may classify it as designed behavior; the finding stands on the fact that the retry decision is controlled by an unprivileged vault user and no alternate unfreeze path exists in `IdleCDOEpochVariant`.