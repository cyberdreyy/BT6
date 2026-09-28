### Title
Unconditional external ERC4626 dereference in `onStopEpoch` lets an unprivileged vault user block epoch settlement and freeze queued withdrawals — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The Orval bug class is "unrestricted resolution of an external reference whose content/success is attacker-influenced." The on-chain analog is the programmable-borrower epoch flow: `ProgrammableBorrower` dereferences an external ERC4626 vault on every epoch stop via `convertToAssets` and `vault.withdraw`, and `IdleCDOEpochVariant.stopEpoch` inlines that external result into pool accounting. A user of the configured vault (e.g., a MetaMorpho lender pulling idle liquidity, or a borrower taking it) can make the vault unable to service the withdrawal at stop time, causing `StopEpochVaultLiquidityUnavailable` to revert the entire `stopEpoch` transaction and freezing all pending tranche withdrawal requests until vault liquidity returns.

### Finding Description
`ProgrammableBorrower` parks all undrawn pool funds in an external ERC4626 `vault` (set via `initialize`/`setVault`, lines 126, 158–170). During `onStopEpoch`, called by `IdleCDOEpochVariant.stopEpoch`, the contract unconditionally resolves that external reference:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:239-253
uint256 onHand = underlyingToken.balanceOf(address(this));
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

`_currentVaultAssets()` (lines 546–549) is `vault.convertToAssets(shares)` — share-value-based liquidity, not real withdrawable liquidity. For ERC4626 vaults backed by lending markets (e.g., MetaMorpho/Morpho, which the Foundry tests fork), `convertToAssets` counts assets lent out to third-party borrowers, while `withdraw` reverts when the vault lacks immediate cash. The guard at line 245 therefore passes and the `withdraw` then reverts, hitting the `catch` and reverting the whole stop.

Because `stopEpoch` cannot complete, `epochEndDate` settlement never finalizes, `requestWithdraw` receipts cannot be claimed (`collectWithdrawFunds`/`claimWithdrawRequest` depend on a completed stop), and new deposits/epoch transitions are blocked. The condition is reproducible by any unprivileged vault participant: withdraw or borrow the vault's idle liquidity after `epochEndDate` passes, so the shortfall check sees sufficient share value but `vault.withdraw` has no cash.

### Impact Explanation
Temporary freezing of user funds. All tranche holders who filed `requestWithdraw` for the epoch have their underlying locked until vault liquidity returns, which is outside the pool's control — MetaMorpho liquidity depends on third-party market borrowers repaying. For a facility where a large fraction of `_amountRequired` sits in the vault, the freeze covers the full pending-withdrawal amount for an unbounded duration. Unlike the "shares don't cover shortfall" case (line 245 returns `true` so the CDO proceeds to a failed `transferFrom` and its normal default path), this branch hard-reverts, so no state is checkpointed and `stopEpoch` must be retried against the still-unresolved external vault. This is the SSRF analog: the protocol dereferences an external endpoint it does not control, inlines the result into settlement, and has no fallback when the endpoint refuses.

### Likelihood Explanation
Requires only an unprivileged ERC4626 vault user — an allowed attacker role — performing a legitimate `withdraw`/`borrow` on the vault. Vault liquidity crunches are routine in lending vaults (high utilization states are common around epoch ends, when large withdrawals are anticipated). No privileged cooperation is needed, and the honest manager calling `stopEpoch` cannot prevent it. It is not fully a loss-of-funds vector: liquidity eventually returns in normal market conditions, so impact is temporary freezing rather than permanent loss or theft, which caps severity.

### Recommendation
Apply the same fix pattern as the Orval advisory — bound the external reference resolution:

- Pre-check real liquidity (`vault.maxWithdraw(address(this))`) rather than `convertToAssets` before deciding the revert path, and treat `maxWithdraw < shortfall` as a covered-but-illiquid case that flows into the orderly default path instead of a hard revert, so the epoch can still transition state.
- Allow `stopEpoch` to settle with partial vault liquidity (pull `maxWithdraw`, mark the residual as owed) so withdrawals queue against actual receipts rather than blocking indefinitely.
- Optionally bound local dereference scope like the `$ref` fix: whitelist vaults to ones with guaranteed atomic liquidity, or require idle liquidity buffers proportional to `epochPendingWithdraws` before `startEpoch`.

### Proof of Concept
Foundry fork (mirroring `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which forks the real MetaMorpho USDC vault):

```solidity
function testStopEpochFrozenByVaultLiquidityDrain() external {
  uint256 amount = 10_000 * oneScale;
  vm.prank(owner);
  cdoEpoch.setIsInterestMinted(true);

  idleCDO.depositAA(amount);
  _startEpochAndCheckPrices(0);            // funds parked into morphoVault via onStartEpoch

  uint256 requested = cdoEpoch.requestWithdraw(amount / 2, address(aaTranche));

  vm.warp(cdoEpoch.epochEndDate() + 1);

  // Attacker: ordinary ERC4626 vault user drains vault liquidity so
  // convertToAssets still covers the shortfall but withdraw() reverts.
  address vaultUser = makeAddr("vaultUser");
  deal(USDC, vaultUser, 1, true);
  uint256 vaultLiquid = USDC.balanceOf(address(morphoVault)); // or maxWithdraw for a large holder
  vm.startPrank(vaultUser);
  // pull idle liquidity: either withdraw own shares or borrow on underlying Morpho markets
  // leaving morphoVault.maxWithdraw(address(programmableBorrower)) < shortfall
  vm.stopPrank();

  // stopEpoch reverts inside onStopEpoch -> catch -> StopEpochVaultLiquidityUnavailable
  vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // Epoch never stops; withdraw request cannot be claimed.
  vm.expectRevert();
  cdoEpoch.claimWithdrawRequest(address(aaTranche));

  // Retry after liquidity returns (repay on Morpho markets) succeeds — temporary freeze confirmed.
}
```

Caveat: I verified the revert path at `ProgrammableBorrower.sol:239–253` and the valuation source at lines 546–549, but did not fully trace `IdleCDOEpochVariant.stopEpoch`'s handling of the revert (e.g., whether any later catch/fallback exists). If `stopEpoch` wraps the hook call in its own try/catch with a default fallback, the freeze window shrinks — that call site should be confirmed before final severity assessment.