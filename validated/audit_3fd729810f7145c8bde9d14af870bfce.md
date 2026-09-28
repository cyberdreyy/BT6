### Title
ERC4626 vault donation inflates `totalInterestDueNow` and mints unbacked epoch interest, letting a large tranche holder drain honest LPs — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The MkDocs-hooks bug class is "attacker-controlled input flows into a privileged execution context through a hook". The credit-vault analog is the programmable-borrower hook surface: `IdleCDOEpochVariant._stopEpoch` trusts `IProgrammableBorrower.totalInterestDueNow()`, which in turn trusts `vault.convertToAssets()` of an external ERC4626 vault. An unprivileged user of that vault can donate underlying to inflate `convertToAssets`, causing the pool to book phantom epoch interest and raising tranche prices beyond real backing.

### Finding Description
`ProgrammableBorrower._vaultNetInterest` computes epoch PnL as `_currentVaultAssets() + epochWithdrawnFromVault − (epochStartVaultAssets + epochDepositedToVault)`, where `_currentVaultAssets()` is `vault.convertToAssets(balanceOf(this))` (lines 337–349, 546–549). `totalInterestDueNow()` adds this to borrower interest (line 330–334) and `IdleCDOEpochVariant._resolveStopEpochInterest` returns it verbatim as `expectedEpochInterest` (`IdleCDOEpochVariant.sol:1001-1008`), which `_updateAccounting` mints into tranche prices.

A donation of `D` underlying directly to the vault raises the borrower's share value by ~`D`, producing `+D` of fake `vaultInterest` even though no vault shares were minted to anyone for it — the `D` itself already sits inside the vault position. So NAV bookkeeping counts `D` twice: once as vault assets, once as accrued epoch interest priced into tranches. `_skimDonatedAssets` (`IdleCDOEpochVariant.sol:355`) only skims tokens sent to the CDO contract itself; it cannot see donations internal to the external vault.

Attack sequence (minted-interest mode, running epoch):
1. Attacker (KYC'd) holds fraction α of tranche supply — acquirable via normal deposits or secondary transfers.
2. During a running epoch, attacker transfers `D` underlying into the ERC4626 vault (direct transfer, no `deposit` call needed for balance-based `totalAssets`).
3. Honest manager calls `stopEpoch`. `totalInterestDueNow()` now includes `+D`, so `expectedEpochInterest` and tranche virtual price rise by `D` on top of the `+D` already inside the vault position.
4. Attacker requests withdrawal / redeems at the inflated price; in non-minted mode the CDO additionally pulls `+D` of real cash from the honest borrower via `getFundsFromBorrower`.

### Impact Explanation
The fair mint/burn invariant breaks: `+D` of tranche value is priced with no corresponding new cash (minted mode) or is paid by the borrower for yield that was actually the attacker's own donation (non-minted mode). Attacker profit ≈ `2αD − D`, positive whenever α > ~50% of pool NAV; the loss is socialized onto the remaining LPs, whose receipts are left under-collateralized at claim time (insolvency for late claimers). With a pre-existing large position or pool of colluding KYC'd holders, `D` is bounded only by attacker capital.

### Likelihood Explanation
Requires: a programmable-borrower deployment, an ERC4626 vault whose `totalAssets` is balance-based (the standard OZ implementation, which the code assumes via `convertToAssets`), and an attacker controlling a majority share of tranche NAV — feasible for whale lenders in concentrated credit pools. The borrower's honest repayment in non-minted mode makes the theft directly cash-out-able. No privileged-role misbehavior is needed; `setVault` is owner-gated but the attack works against the legitimately configured vault.

### Recommendation
Track vault PnL against share-denominated baselines instead of raw `convertToAssets` deltas, or treat the external vault share price as untrusted: e.g., cap recognized `vaultInterest` per epoch at the APR-derived expected yield (`maxApr` style bound already used at `IdleCDOEpochVariant.sol:379`), or snapshot a price-per-share at `onStartEpoch` and compute interest as `shares * (priceNow − priceStart)` with a sanity bound. Alternatively skim/ignore vault-side donations by comparing `convertToAssets` gains against a deviation threshold.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: fork mainnet, deploy IdleCDOEpochVariant + IdleCreditVault with
// isProgrammableBorrower = true, isInterestMinted = true, attacker seeded
// with >= 60% of tranche supply via depositAA/depositBB during buffer.

function testVaultDonationInflatesEpochInterest() public {
    // 1. manager startEpoch -> onStartEpoch deposits pool funds into vault
    vm.prank(manager); cdo.startEpoch();

    uint256 navBefore = cdo.getContractValue();
    uint256 priceBefore = cdo.virtualPrice(address(aaTranche));

    // 2. attacker donates D underlying directly to the external ERC4626 vault
    uint256 D = 1_000_000e6;
    deal(address(underlying), attacker, D);
    vm.prank(attacker);
    underlying.transfer(address(externalVault), D); // no shares minted

    // 3. warp past epochEndDate; manager stops epoch
    vm.warp(epochEndDate + 1);
    uint256 reported = borrowerContract.totalInterestDueNow();
    // reported includes +D of phantom vaultInterest
    vm.prank(manager);
    cdo.stopEpoch(0, 0);

    // 4. tranche price inflated by ~D beyond real yield
    uint256 priceAfter = cdo.virtualPrice(address(aaTranche));
    assertGt(priceAfter - priceBefore, expectedRealYieldDelta);

    // 5. attacker requests+claims withdrawal, capturing >D of pool value
    // remaining LPs' claims are left under-collateralized by ~ (1-alpha)*2D - D
}
```

Note: this is a conceptual analog — validity depends on the configured vault using a balance-based `totalAssets` (donation-susceptible) implementation; vaults with internal asset accounting are not exploitable this way.