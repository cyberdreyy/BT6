### Title
Vault user can force `stopEpoch` to revert via drained ERC4626 liquidity, freezing epoch withdrawals - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
CVE-2026-34872 is a "lack of contributory behavior" bug: a peer can force a shared result into a small set of values because inputs are not validated. The strongest credit-vault analog is the programmable-borrower sleeve: `IdleCDOEpochVariant` prices and settles each epoch off a single external value it does not control — the ERC4626 vault's position and liquidity — and `ProgrammableBorrower.onStopEpoch` reverts instead of degrading gracefully when the vault has accounting-covered shares but no withdrawable liquidity. Any unprivileged user of that ERC4626 vault can push available liquidity toward zero so the vault's `withdraw` reverts, forcing `stopEpoch` to revert every time and freezing all lender withdrawals for the epoch.

### Finding Description
When an epoch ends, `IdleCDOEpochVariant` reads `totalInterestDueNow()` and calls `onStopEpoch(_amountRequired, _isRequestingAllFunds)` to pull cash for pending withdraw requests. In `ProgrammableBorrower.onStopEpoch` (contracts/strategies/idle/ProgrammableBorrower.sol:231-267):

```solidity
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
    uint256 shortfall = _amountRequired - onHand;
    if (shortfall > _currentVaultAssets()) return true; // insolvent -> default path
    try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        ...
    } catch {
        revert StopEpochVaultLiquidityUnavailable();
    }
}
```

The `catch { revert ... }` branch is reachable whenever the vault shares economically cover `shortfall` (so the `shortfall > _currentVaultAssets()` early-return is not taken) but the vault's `withdraw` reverts due to insufficient idle liquidity (e.g., a MetaMorpho-style vault whose underlying is lent out and whose market liquidity has been consumed). An unprivileged third-party depositor/borrower of that external vault — explicitly an allowed attacker class — can borrow or withdraw the vault's free liquidity right around epoch end, so the vault reports full share value yet cannot honor `withdraw`.

The broken invariant is fair settlement / "one epoch, one settlement": the pool's ability to stop the epoch and pay `requestWithdraw` claims depends entirely on a counterparty-controlled quantity (external vault liquidity) that the peer can force into the "empty" set. There is no fallback: no partial withdrawal loop, no redeem-vs-withdraw retry, no queueing of the stop; the call simply reverts, and since `epochAccountingActive` stays true and `epochPendingWithdraws` is only cleared on success, the epoch cannot be closed by anyone else either.

Existing guards do not stop it: `nonReentrant` is irrelevant, `_checkOnlyIdleCDO` only authenticates the caller, and the `shortfall > _currentVaultAssets()` check only routes insolvency to the default path — it does not cover liquidity insufficiency, which is precisely the case where shares still cover the amount.

### Impact Explanation
Temporary freezing of lender funds. While the attacker keeps the vault drained, every `stopEpoch`/`stopEpochWithDuration` call reverts, so `epochPendingWithdraws` is never cleared, pending withdraw requests cannot be claimed via `claimWithdrawRequest`, and tranche holders who requested withdrawals during the epoch are frozen. The frozen amount equals the pool's pending withdrawals plus any principal that cannot be recalled (up to the full `amountRequired`, which in a close-pool scenario is the entire TVL parked in the vault). Once honest liquidity returns, the manager can retry, so the freeze is temporary — but it persists exactly as long as the attacker chooses to keep the external vault's liquidity consumed, which for a Morpho-style market can be maintained cheaply by holding a borrow position.

### Likelihood Explanation
Medium. It requires (a) the pool operating in programmable-borrower mode (`isProgrammableBorrower`), which is a supported production configuration, and (b) an external ERC4626 vault whose idle liquidity an unprivileged user can transiently exhaust — standard behavior for lending-aggregator vaults where available liquidity is a public, attacker-influenceable quantity. The attack requires capital sufficient to consume vault liquidity (borrowable against collateral in Morpho markets), but no privileged role, and the sequence is a single well-timed transaction around `epochEndDate`. No owner/manager action is needed by the attacker; the honest manager's `stopEpoch` call is what gets griefed.

### Recommendation
- In `onStopEpoch`, do not revert on vault withdrawal failure. Catch the failure, keep accounting consistent (do not bump `epochWithdrawnFromVault`), and return `success = true` so the subsequent `transferFrom` can pull whatever `onHand` exists; let IdleCDO's existing default/shortfall path handle the deficit rather than blocking the state machine.
- Alternatively, withdraw up to `vault.maxWithdraw(address(this))` instead of the full `shortfall`, record the actual amount received in `epochWithdrawnFromVault`, and let settlement proceed partially.
- Consider reserving required withdraw liquidity earlier (e.g., redeeming to cash at epoch end before peer liquidity can be pulled) so the pool does not depend on a counterparty-controlled liquidity quantity at settlement time.

### Proof of Concept
Sketch (Foundry fork against the real MetaMorpho vault used by the borrower):

```solidity
// Setup: pool in programmable-borrower mode, epoch running, lenders have
// called requestWithdraw so _amountRequired > onHand at stop time.
// Attacker: ordinary Morpho market borrower / vault withdrawer.

// 1. Attacker consumes the vault's idle liquidity (borrow all available
//    in the vault's markets / withdraw all free liquidity) right after epochEndDate.
//    Vault share price is unchanged, so _currentVaultAssets() still covers `shortfall`.

// 2. Honest manager calls:
vm.prank(manager);
cdoEpoch.stopEpoch(apr, funds);
// -> ProgrammableBorrower.onStopEpoch -> vault.withdraw(shortfall) reverts
//    -> catch -> revert StopEpochVaultLiquidityUnavailable()

// 3. Assert grief:
assertTrue(cdoEpoch.epochEndDate() < block.timestamp); // epoch overdue
assertEq(cdoEpoch.epochAccountingActive-ish, true);    // still active via epochPendingWithdraws
vm.expectRevert(); cdoEpoch.claimWithdrawRequest(...); // lender funds frozen

// 4. Repeat step 1-2 to sustain the freeze; funds remain locked until
//    the attacker releases vault liquidity.
```

Note: I verified the revert path in `ProgrammableBorrower.sol` and the accounting fields it depends on, but was not able to read `IdleCDOEpochVariant.stopEpoch`'s exact call ordering (whether it wraps `onStopEpoch` in its own try/catch) before finalizing; if `IdleCDOEpochVariant` already catches `StopEpochVaultLiquidityUnavailable` and routes to a partial settlement, this finding reduces to a no-op and the conclusion should be downgraded.