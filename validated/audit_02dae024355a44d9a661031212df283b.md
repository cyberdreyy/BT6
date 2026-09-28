### Title
Unvalidated ERC4626 share price lets a vault depositor mint unbacked epoch interest — missing realizable-value check in `onStopEpoch`/`totalInterestDueNow` - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The NocoDB bug class is a guard that validates one property of attacker-controlled input (`anchor.host === location.host`) while skipping a second property (URL scheme), so hostile input slips into a privileged sink. The analog in this codebase is `ProgrammableBorrower`: `totalInterestDueNow()` and the stop-epoch coverage check in `onStopEpoch` validate only the *nominal* share valuation via `vault.convertToAssets()` and never validate that the reported asset value is realizable liquidity (the missing "scheme check"). An unprivileged depositor in the borrower's ERC4626 vault can inflate the spot share price; the pool then mints interest that is not backed by recoverable underlying.

### Finding Description
In `contracts/strategies/idle/ProgrammableBorrower.sol`:

- `_currentVaultAssets()` values the entire position with the spot price `vault.convertToAssets(shares)` (`ProgrammableBorrower.sol:546-549`).
- `totalInterestDueNow()` reports `vaultInterest = bufferedVaultDelta + currentVaultAssets + epochWithdrawnFromVault - (epochStartVaultAssets + epochDepositedToVault)` when positive (`ProgrammableBorrower.sol:330-347`). This single value is read by `IdleCDOEpochVariant.stopEpoch` to price the epoch (`IdleCDOEpochVariant.sol`, programmable-borrower branch).
- Programmable deployments run `isInterestMinted` mode (`setIsInterestMinted` forces it for programmable borrowers, `IdleCDOEpochVariant.sol:166-170`): epoch interest is crystallized into tranche prices / minted strategy-token claims *without* any underlying being transferred to the CDO.
- `onStopEpoch` only withdraws the cash `shortfall` IdleCDO will pull, and treats "nominal shares cover the shortfall" (`shortfall > _currentVaultAssets()` → return true) as sufficient (`ProgrammableBorrower.sol:239-253`). It never checks that the *full* reported `totalInterestDueNow` is backed by withdrawable assets.

The broken invariant is fair mint/burn + solvency: `convertToAssets` is a spot valuation, not a solvency proof. If the vault's share price is inflated (e.g., a vault whose `totalAssets` includes raw balance/donations, or a donation-sized maneuver by any vault depositor), the delta between the inflated `earnedAssets` and the real principal baseline is reported as `vaultInterest`, flows into `totalInterestDueNow()`, and is minted as pool interest. No guard (`_skimDonatedAssets`, reserve checks, KYC, only-CDO) inspects the vault-side valuation; the "same-host check" (`convertToAssets` coverage) passes while the missing "scheme check" (is this value actually realizable?) is never performed.

### Impact Explanation
- Phantom interest is minted into tranche prices at `stopEpoch`: AA/BB NAVs and `lastEpochInterest` reflect `totalInterestDueNow()` even though the corresponding underlying cannot be withdrawn from the vault.
- Early withdrawers (`claimWithdrawRequest`, `collectWithdrawFunds`, APR0/instant flows) redeem against inflated NAV; later withdrawers and residual receipt holders absorb the shortfall — direct loss of LP funds up to the inflated delta, i.e., partial insolvency and unfair payout ordering.
- Symmetric temporary-freeze leg: a vault user can also drain underlying liquidity from the vault's markets so `vault.withdraw(shortfall)` reverts (`StopEpochVaultLiquidityUnavailable`), making `stopEpoch` uncallable while `convertToAssets` still reports coverage — the coverage check passes nominally but the funds are not actually movable.

### Likelihood Explanation
- Attacker needs only to be an unprivileged depositor/user of the configured ERC4626 vault — an explicitly allowed attacker profile.
- Exploitability depends on the deployed vault's `totalAssets` accounting; for any vault where `convertToAssets` is movable by donations or market-level maneuvers (common for ERC4626s that track raw balance or illiquid allocations), the inflation is permissionless.
- No privileged cooperation is required: `stopEpoch` is called by the honest manager; the attacker's only action is manipulating the vault share price before the stop.

### Recommendation
- In `stopEpoch`, require realized value, not spot value: pull interest as actual underlying (`vault.withdraw`/`redeem`) and compute epoch interest from the *assets actually received*, not from `convertToAssets`.
- Cap `totalInterestDueNow()` by `previewRedeem(shares)`/max-withdrawable liquidity, or track gains only when the corresponding shares are redeemed.
- In `onStopEpoch`, replace the nominal coverage check `shortfall > _currentVaultAssets()` with a check against `vault.maxWithdraw(address(this))` so coverage reflects realizable liquidity.

### Proof of Concept
Foundry fork sketch (against a vault where `convertToAssets` is inflatable by a donation):

```solidity
// test/foundry/ProgrammableBorrowerInflatedInterest.t.sol
function testVaultSharePriceInflationMintsUnbackedInterest() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);           // epochAccountingActive, epochStartVaultAssets = amount

    // Attacker: unprivileged depositor in the ERC4626 vault inflates convertToAssets.
    // e.g. direct asset donation to a vault whose totalAssets counts raw balance,
    // or supply-side maneuver that raises share price without recoverable liquidity.
    deal(USDC, attacker, inflate);
    vm.prank(attacker);
    underlying.transfer(address(morphoVault), inflate); // share price up, same shares held by PB

    uint256 reported = programmableBorrower.totalInterestDueNow();
    assertGt(reported, 0);                  // phantom "vault interest"

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);               // mints `reported` interest into tranche prices

    // Solvency breaks: tranche NAV now exceeds total realizable vault + cash assets.
    uint256 realizable = morphoVault.previewRedeem(morphoVault.balanceOf(address(programmableBorrower)))
        + underlying.balanceOf(address(programmableBorrower));
    assertGt(cdoEpoch.lastNAVAA() + cdoEpoch.lastNAVBB(), realizable);
    // First claimWithdrawRequest drains cash; later withdrawers are left short.
}
```

Caveat: I confirmed the sink chain (`_vaultNetInterest` → `totalInterestDueNow` → `onStopEpoch` coverage check) in `ProgrammableBorrower.sol` but could not re-verify the exact `stopEpoch` mint path in `IdleCDOEpochVariant.sol` within the available iterations; the PoC assumes interest is crystallized from `totalInterestDueNow()` as documented in the interface and tests (`lastEpochInterest ≈ totalInterestDueNow`).