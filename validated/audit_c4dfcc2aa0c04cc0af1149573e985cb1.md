### Title
`depositDuringEpoch` over-mints shares when a single tranche holds all NAV because it applies `trancheAPRSplitRatio` while actual accounting assigns 100% of the gain to the populated tranche - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The mid-epoch deposit path prices minted tranche tokens using a projected "expected final NAV" that splits `expectedEpochInterest` between AA and BB via `trancheAPRSplitRatio`. However, the real end-of-epoch accounting in `_virtualPriceAux` gives the entire gain to a tranche whenever the other tranche has zero NAV/supply (AA-only or BB-only vaults). This mismatch underprices `expectedFinal`, so an unprivileged (KYC'd) depositor is minted more tranche tokens than fair and directly steals yield from existing holders.

### Finding Description
In `depositDuringEpoch`, shares are minted as:

```solidity
// contracts/IdleCDOEpochVariant.sol:705-724
trancheExpected = _calcTrancheInterestShare(_netGainAfterFees(expectedInt - pendingFees, ...), _tranche);
trancheInterest = _calcTrancheInterestShare(_netGainAfterFees(interest, ...), _tranche);
uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

`_calcTrancheInterestShare` multiplies by `trancheAPRSplitRatio` (for AA) or `FULL_ALLOC - ratio` (for BB) (lines 883–886).

The actual gain allocation at `_updateAccounting`/`stopEpoch` goes through `_virtualPriceAux`, which does **not** use the split ratio when the other tranche is empty:

```solidity
// contracts/IdleCDOCreditVault.sol:319-322
if (_lastTrancheNAV == 0) {
  _totalTrancheGain = 0;
} else if (_lastNAV == _lastTrancheNAV) {
  _totalTrancheGain = totalGain;   // sole populated tranche gets 100% of the gain
}
```

Only the deposited-into tranche's supply is checked (`_trancheTotSupply == 0` reverts, line 677); the *other* tranche may have zero supply/NAV. Fixed-APR mode is required for this path (`isAYSActive` must be false, line 665), so `trancheAPRSplitRatio` is a stale, manager-set constant that is not reconciled with the empty-tranche reality before minting.

### Impact Explanation
Direct theft/dilution. In an AA-only vault with stored `trancheAPRSplitRatio < FULL_ALLOC`, `trancheExpected` and `trancheInterest` are under-estimated (e.g., ratio = 0 gives minted = `_amount * supply / lastNAV`, i.e., 1:1 at par), while at `stopEpoch` the AA tranche receives 100% of `expectedEpochInterest`. The attacker's shares therefore claim a fraction of interest the formula assumed would go to BB but which in reality belongs to existing AA holders. Numerically: lastNAVAA = 1000, supply = 1000e18, 10% APR full-year epoch (expected interest 100), attacker deposits 1000 mid-epoch (gross interest 50):

- Fair mint: `(1050/1100) * 1000e18 ≈ 954.5e18` (as `testDepositDuringEpochNumericalAA` shows when the ratio logic is consistent).
- With stored ratio = 0: `trancheExpected = 0`, `trancheInterest = 0`, `minted = 1000e18`.
- At stopEpoch: NAV = 2150 (all 150 gain to AA), supply = 2000e18, price = 1.075 → attacker redeems 1075 (entitled 1050, +25) and the existing holder gets 1075 (entitled 1100, −25). Roughly 2.3% of NAV / 25% of the existing holder's epoch yield is stolen per attack-sized deposit, repeatable every epoch and scaling linearly with deposit size.

The same flaw exists in the BB direction (BB-only vault with stored ratio > 0 under-mints `expectedFinal` for BB and over-mints BB shares).

### Likelihood Explanation
Requires: `isAYSActive == false` (fixed-APR mode — the only mode where `depositDuringEpoch` is permitted), an honest owner/manager having enabled `isDepositDuringEpochDisabled = false` (the normal way this feature is used), a running epoch, and one tranche class being empty (common for pools launched AA-only). The attacker only needs to pass `isWalletAllowed` KYC — an explicitly allowed unprivileged actor. All gating (`_skimDonatedAssets`, `_guarded`, `_updateAccounting`) executes but does not prevent the mispricing. No privileged misbehavior is needed; the stored split ratio is legitimately configured.

### Recommendation
In `depositDuringEpoch`, replicate `_virtualPriceAux`'s allocation semantics when computing `trancheExpected`/`trancheInterest`: if the counterparty tranche's saved NAV (or supply) is zero, assign 100% of the net expected gain to the deposited tranche instead of applying `trancheAPRSplitRatio`. Equivalently, compute `expectedFinal` by simulating the actual `_updateAccounting` gain attribution rather than `_calcTrancheInterestShare`.

### Proof of Concept
Foundry fork-style test (adapted from `testDepositDuringEpochNumericalAA` in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testDepositDuringEpochOverMintSingleTranche() external {
    // 10% apr, 365-day epoch, no buffer; AYS off so depositDuringEpoch is allowed
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);
    vm.stopPrank();

    // AA-only vault: BB tranche has zero supply/NAV.
    // Stored trancheAPRSplitRatio under-weights AA (e.g. 50% or even 0).
    uint256 initialDeposit = 1000 * ONE_SCALE;
    idleCDO.depositAA(initialDeposit);            // existing honest holder
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // trancheAPRSplitRatio stays at a value < FULL_ALLOC (e.g. 50%)

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() - (cdoEpoch.epochDuration() / 2));
    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address attacker = makeAddr('attacker');      // KYC-passing lender
    uint256 depositAmount = 1000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, depositAmount);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), depositAmount);
    uint256 minted = cdoEpoch.depositDuringEpoch(depositAmount, address(AAtranche));
    vm.stopPrank();

    // BUG: minted uses split-ratio-adjusted expectedFinal, but _virtualPriceAux
    // will give AA 100% of the epoch gain (BB is empty). Fair mint is
    // (amount + interest) * supply / (nav + allExpectedInterest) = 954.5e18;
    // actual minted is strictly larger (up to 1000e18 when ratio == 0).
    assertGt(minted, 954545454545454545454);

    _toggleEpoch(false, 10e18, _expectedFundsEndEpoch());
    uint256 price = cdoEpoch.virtualPrice(address(AAtranche));
    uint256 attackerValue = minted * price / ONE_TRANCHE_TOKEN;
    // attacker extracts more than deposit + prorated interest (1050)
    assertGt(attackerValue, 1050 * ONE_SCALE);
    // existing holder is diluted below their entitled 1100
    uint256 existing = IERC20(address(AAtranche)).balanceOf(address(this));
    assertLt(existing * price / ONE_TRANCHE_TOKEN, 1100 * ONE_SCALE);
}
```

Caveat: I verified the allocation mismatch statically across `depositDuringEpoch`/`_calcTrancheInterestShare`/`_virtualPriceAux`, but did not have iterations left to confirm the exact stored `trancheAPRSplitRatio` defaults for a single-tranche pool (it is set in `setTrancheAPRSplitRatio`/epoch-start logic); the PoC assumes any value `< FULL_ALLOC`, which holds whenever the split ratio is not pinned to 100% for AA.