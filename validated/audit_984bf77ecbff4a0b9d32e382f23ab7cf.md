### Title
Programmable-borrower stopEpoch trusts a live external ERC4626 valuation — a third-party vault user can brick epoch settlement and freeze all LP funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` feeds an unvalidated, revertable external signal — `vault.convertToAssets(shares)` — directly into the credit vault's epoch-settlement path. `totalInterestDueNow()` is read by `IdleCDOEpochVariant.stopEpoch` before `onStopEpoch` runs, and it internally calls `_vaultNetInterest()` → `_currentVaultAssets()` → `vault.convertToAssets()` with no try/catch and no cached fallback [1](#0-0) . This is the on-chain analog of CVE-2018-8038: the system "parses" attacker-influenced external content (the ERC4626's live valuation) without disabling the dangerous behavior — a valuation view that any vault participant can push into a reverting or manipulated state.

### Finding Description
The bug class is *processing untrusted external input on a critical path without isolating failure*. In `ProgrammableBorrower`:

- `_currentVaultAssets()` calls `vault.convertToAssets(vault.balanceOf(address(this)))` unconditionally [2](#0-1) .
- `totalInterestDueNow()` and `vaultInterestAccrued()`/`vaultLoss()` all route through `_vaultNetInterest()`, which adds `_currentVaultAssets() + epochWithdrawnFromVault` [3](#0-2) .
- `availableToBorrow()` and `onStopEpoch()` also call `_currentVaultAssets()` [4](#0-3) .
- Only `vault.withdraw` inside `onStopEpoch` is wrapped in try/catch (`StopEpochVaultLiquidityUnavailable`); every *view* valuation is naked [5](#0-4) .

The codebase itself concedes the hazard: `onDefault()` deliberately avoids `convertToAssets` with the comment "Even if the external vault's valuation view is unavailable during stress, CDO default handling must still be able to shut down borrowing" [6](#0-5) . That defensive pattern was applied to the default path but not to the normal stop path, which is the one that runs every epoch.

Because `stopEpoch` reads `totalInterestDueNow()` first (per the comment at line 256: "`totalInterestDueNow()` was already read by IdleCDO before calling this hook"), any revert inside `convertToAssets` aborts the entire stop transaction before `onStopEpoch`, before `sendFundsToBorrower`, and before `epochNumber` is incremented. Withdrawal claims gated on `epochNumber > lastWithdrawRequest` in `IdleCreditVault._claimFundedWithdrawRequest` can never unblock [7](#0-6) . `startEpoch` is likewise unusable since `isEpochRunning` stays true.

Who can break the valuation? Any unprivileged user of the borrower's ERC4626 vault — an explicitly in-scope attacker. Depending on the vault implementation this includes: pushing the vault into an unsettled/paused epoch state (async vaults), triggering a share-price state where `convertToAssets` overflows or reverts, or griefing via mechanics that make `totalAssets` revert (e.g., vaults reading a market/settlement that an attacker can leave in an inconsistent state). Even transient or honest vault downtime produces the same freezing, since there is no degraded path.

There is a second, non-revert variant: `_vaultNetInterest` computes `netDelta` against `epochStartVaultAssets`, which was snapshotted at `onStartEpoch` [8](#0-7) . An external vault user who can transiently manipulate `convertToAssets` (flash-donation into the ERC4626 around `stopEpoch`) inflates `vaultInterest` for that block, raising `totalInterestDueNow()` and the `transferFrom` pulled from the facility — converting a price manipulation into a real NAV/loss mispricing crystallized into tranche prices by `_updateAccounting`/`_virtualPriceAux` [9](#0-8) . Unlike the CDO's own `_skimDonatedAssets` defense against raw-token donations [10](#0-9) , there is no equivalent isolation for donations routed through the external vault's share price.

### Impact Explanation
- **Permanent/temporary freezing of all LP funds**: if `convertToAssets` reverts persistently, `stopEpoch` can never succeed, `epochEndDate` never resets, pending withdraw receipts stay unclaimable (`epochNumber <= lastWithdrawRequest` check), and tranche holders cannot exit — all vault-held principal and borrower principal is frozen with no recovery path other than owner `rescueTokens`, which does not restore tranche accounting.
- **Insolvency/theft via price manipulation**: a flash-inflated `convertToAssets` at `stopEpoch` misprices the epoch interest and the subsequent `transferFrom`; conversely a deflated reading mints a phantom `vaultLoss` that is socialized across tranches via the BB-first waterfall while the attacker later unwinds their vault position at par.
- Quantification: in the freeze case, 100% of facility TVL (on-hand plus `vault` position) is locked; in the manipulation case, the loss equals the delta between manipulated and true vault valuation applied to `totalInterestDueNow`, bounded by total epoch interest plus the swingable share of principal.

### Likelihood Explanation
- The vault is a third-party ERC4626 the borrower facility parks all undrawn capital in; unprivileged depositors of that vault can influence `totalAssets`/settlement state in many real vault designs (async settlement vaults, vaults with pausable valuation, vaults susceptible to share-price inflation before first large deposit). The in-scope threat model explicitly includes "a user of the programmable borrower's ERC4626 vault."
- `onStopEpoch`'s own comment (line 245) shows the authors anticipated vault-share insufficiency but only guarded the `withdraw` call, not the valuation reads that gate it.
- No privileged cooperation is needed: the attack is executed entirely by transactions against the external vault timed around the honest manager's `stopEpoch` call.
- The main uncertainty is vault-specific: for a plain OpenZeppelin ERC4626 over a simple asset, forcing `convertToAssets` to revert is hard (requires overflow or vault pause), so severity there reduces to the donation/manipulation variant. For async or settlement-based vaults (the realistic deployment target for a "programmable borrower" parking facility), both variants are practical.

### Recommendation
- Read the external vault valuation defensively: wrap `vault.convertToAssets` in a `try` inside a dedicated view, and on failure fall back to the last cached valuation (`epochStartVaultAssets + epochDepositedToVault - epochWithdrawnFromVault` or a stored last-good value) so `stopEpoch` remains callable; mirror the existing `onDefault` precedent.
- Cache `epochStartVaultAssets`-style baselines and use share-balance math (`shares * lastPrice` with price sanity bounds) rather than live `convertToAssets` at settlement time.
- Bound `vaultInterest`/`vaultLoss` deltas per stop (e.g., cap to a configured max drawdown/deviation) and route outsized deltas through the owner/manager `stopEpochWithDuration` loss path instead of silently crystallizing them into tranche prices.
- Treat donations to the underlying ERC4626 like direct donations to the CDO: value the sleeve on shares outstanding vs. a checkpointed price, not instantaneous `totalAssets`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
// Foundry fork PoC: assume pool is mid-epoch in programmable mode; all capital
// parked in `vault` (the ProgrammableBorrower's ERC4626). Attacker is any
// depositor of that vault.

function test_stopEpochBrickedByVaultValuation() external {
    // --- setup (pre-state, done by honest owner/manager) ---
    // idleCDO.startEpoch() already ran; ProgrammableBorrower.onStartEpoch()
    // deposited idle cash into `vault` and set epochAccountingActive = true.

    // --- attacker action (unprivileged vault user) ---
    // Drive the ERC4626 into a state where convertToAssets reverts.
    // Example for an async/settlement vault: open a settlement epoch or
    // trigger a state that makes totalAssets() revert. For a vanilla vault:
    // vm.mockCallRevert used here to model the same external condition:
    vm.mockCallRevert(
        address(vault),
        abi.encodeWithSelector(IERC4626.convertToAssets.selector),
        "vault valuation unavailable"
    );

    // --- honest manager tries to end the epoch after epochEndDate ---
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(); // revert inside totalInterestDueNow -> _currentVaultAssets
    cdoEpoch.stopEpoch(0, 0);

    // Every subsequent attempt reverts the same way: epochNumber never
    // increments, epochEndDate stays set, so:
    assertTrue(cdoEpoch.isEpochRunning());

    // Pending withdraw receipts are permanently unclaimable:
    vm.expectRevert(NotAllowed.selector);
    vm.prank(address(cdoEpoch));
    creditVault.claimWithdrawRequest(alice); // epochNumber <= lastWithdrawRequest

    // LP funds (vault shares + borrower principal) are frozen; the only escape
    // is owner rescueTokens on ProgrammableBorrower, which does not restore
    // epoch accounting or let tranche holders exit.
}
```

The donation variant PoC is analogous: `deal(underlying, address(vault), donation)` (or attacker deposit then share-price inflation) immediately before `stopEpoch`, assert `totalInterestDueNow()` moved by the donation amount and that the resulting `transferFrom`/tranche prices in `_updateAccounting` reflect the manipulated delta rather than true vault PnL.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-219)
```text
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L289-300)
```text
  function onDefault() external nonReentrant {
    _checkOnlyIdleCDO();
    if (!epochAccountingActive) return;

    bufferedVaultDelta = 0;
    bufferInterest = 0;
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L233-240)
```text
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);

    // Ordinary losses exhaust BB before reducing AA. Stop normal interactions once BB is wiped,
    // or when an AA-only vault is fully wiped, so the loss must be crystallized explicitly.
    if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```
