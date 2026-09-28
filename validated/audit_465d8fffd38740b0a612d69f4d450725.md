### Title
Phantom vault interest via ERC4626 `convertToAssets` inflation mints unbacked strategy tokens and inflates tranche prices - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`IdleCDOEpochVariant.stopEpoch` resolves epoch interest from `IProgrammableBorrower.totalInterestDueNow()`, which internally values the facility's ERC4626 position with `vault.convertToAssets(shares)` (`ProgrammableBorrower._currentVaultAssets`). An unprivileged third party who is also a depositor in that ERC4626 vault can inflate `convertToAssets` (direct asset donation to the vault, or any price-manipulable asset the vault counts) right before the honest owner/manager calls `stopEpoch`. In minted-interest mode (`isInterestMinted == true`, mandatory for programmable borrowers), the phantom "vault interest" is converted into freshly minted strategy tokens via `_strategy.mintStrategyTokens(_grossInterest)` with no real cash pulled, permanently inflating tranche NAV/price. `_skimDonatedAssets` only skims raw underlying sitting on the CDO itself and does not detect donations inside the external vault, so the guard does not stop it.

### Finding Description
- `totalInterestDueNow()` returns `vaultInterest + borrowerInterestAccruedNow + bufferInterest - loss` where `vaultInterest` comes from `_vaultNetInterest()`, which computes `bufferedVaultDelta + _currentVaultAssets() + epochWithdrawnFromVault - epochStartVaultAssets - epochDepositedToVault` (`ProgrammableBorrower.sol:330-347`, `_currentVaultAssets` at `546-549`). `convertToAssets` on a standard ERC4626 is `totalAssets/totalSupply`-based and is directly manipulable by any EOA transferring the underlying asset to the vault.
- `_resolveStopEpochInterest` (programmable mode) sources `_interest` from `totalInterestDueNow()`; `_grossInterest` then drives `_strategy.mintStrategyTokens(_grossInterest)` and `_updateAccounting()` at `IdleCDOEpochVariant.sol:373-436`. No cap exists against `expectedEpochInterest` when `_interest` resolves internally — the `maxApr` bound at `:377-380` only applies to an explicit `_interest > 1` override.
- Donation isolation fails: `_skimDonatedAssets()` (`IdleCDOEpochVariant.sol:794-796`) sweeps only `token.balanceOf(address(this))`; value injected inside the ERC4626 vault is invisible to it.
- Attack (epoch running, programmable + minted mode): attacker is a KYC'd AA holder and a depositor in the ERC4626 vault. Before `stopEpoch`, they flash-donate `X` underlying to the vault → `convertToAssets` rises → `totalInterestDueNow` rises by ~`X`. Manager calls `stopEpoch(0,0)`; the CDO mints `X` unbacked strategy tokens, `_updateAccounting` distributes the phantom gain to tranches (in this vault all yield effectively accrues per `trancheAPRSplitRatio`; AA gets `ratio` share), raising `priceAA`/`priceBB`. After the stop, the attacker (i) reclaims most of `X` by redeeming their own vault shares — the donation is largely recoverable — and (ii) requests/claims withdrawals at the inflated tranche price, draining real principal. The minted strategy tokens have no cash backing, so the vault is left insolvent by roughly `X * attacker_tranche_share` minus any residual vault gain they forgo.

### Impact Explanation
Direct insolvency/theft: phantom interest is crystallized into `lastNAVAA`/`lastNAVBB` and `priceAA`/`priceBB`, so tranche holders withdraw more underlying than exists; the shortfall is socialized to the last withdrawers / pending withdraw requests (`withdrawsRequestsByEpoch` are paid from real strategy liquidity). Quantified loss is bounded only by how much the attacker can push `convertToAssets` (e.g., flash-loan sized donation or a manipulable vault asset), up to the full TVL once the CDO pays inflated redemptions against real cash.

### Likelihood Explanation
Requires a programmable-borrower deployment whose ERC4626 vault has a spot-manipulable `convertToAssets` (donation-susceptible `totalAssets`, or vault holding a manipulable asset), plus a `stopEpoch` by the honest owner/manager — a routine, scheduled call the attacker can simply sandwich. No privileged collusion needed; the attacker needs KYC (to hold tranches) and vault access, both in-scope. If the vault's `convertToAssets` is not manipulable, the bug is not exploitable.

### Recommendation
- In `ProgrammableBorrower`, cap `totalInterestDueNow()` to `borrowerInterestAccruedNow() + bufferInterest +` a bounded vault-yield estimate (e.g., `vaultShares * maxPricePerShare` using a stored/hardened price), or value the vault position with a TWAP/rate-limited price rather than spot `convertToAssets`.
- Alternatively, in `IdleCDOEpochVariant._stopEpoch`, clamp the resolved programmable interest to `expectedEpochInterest` computed from the configured APR, and treat any excess as donated value (skim or ignore).
- Record `epochStartVaultAssets` in shares-terms with a price snapshot and recognize interest only up to a sanity bound.

### Proof of Concept
Foundry fork PoC outline (programmable borrower + `isInterestMinted = true`, mock/forked ERC4626 `V` whose `totalAssets` is raw balance-based):

```solidity
// setup: AA/BB deposits, startEpoch -> ProgrammableBorrower.onStartEpoch deposits to V
// attacker: holds AA tranches + vault shares in V

// 1) warp to epochEndDate
vm.warp(cdo.epochEndDate());

// 2) attacker inflates vault price: donate D underlying directly to V
underlying.transfer(address(V), D);            // convertToAssets += D per share
// (if V counts attacker's own shares, attacker recovers ~D * attackerShareOfVault later)

uint256 interest = pb.totalInterestDueNow();   // inflated by ~D * pbShare
assertGt(interest, expectedContractualInterest);

// 3) honest manager stops epoch
vm.prank(manager);
cdo.stopEpochWithDuration(newApr, 0, duration, 0);
// -> mintStrategyTokens(grossInterest) mints D-worth of unbacked strategyTokens
// -> _updateAccounting raises lastNAVAA/priceAA

// 4) attacker redeems vault shares to recover donation
V.redeem(V.balanceOf(attacker), attacker, attacker);

// 5) attacker claims inflated tranche value
cdo.requestWithdraw(attackerAABalance, AAtranche);
// ... next epoch stop -> claimWithdrawRequest pays real cash at inflated price

// assert: sum of payouts > real underlying available => insolvency
assertLt(realBacking, totalOwedToTrancheHolders);
```

Assertions to encode the broken invariant: `strategyToken` minted at stop exceeds real vault PnL (`getContractValue()` after stop > actual recoverable underlying), and the attacker's withdrawal value exceeds their pro-rata pre-manipulation share.

Caveat: exploitability depends on the chosen ERC4626 vault's `convertToAssets` being spot-manipulable; the code places no defensive bound on this input, so the finding holds wherever that assumption fails.