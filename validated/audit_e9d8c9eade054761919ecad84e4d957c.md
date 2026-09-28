### Title
Mid-epoch deposits are over-minted in single-tranche vaults because `depositDuringEpoch` splits expected interest by `trancheAPRSplitRatio` while the waterfall gives 100% to the only live tranche - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.depositDuringEpoch` prices newly minted tranche shares using `expectedFinal = lastSavedNAV(tranche) + trancheExpected`, where `trancheExpected` is computed via `_calcTrancheInterestShare` as `expectedInterest * trancheAPRSplitRatio` for AA (or `1 - ratio` for BB). This mirrors the MBUToken bug class: a small deposit is priced against a stale/incorrect NAV denominator and mints far more claim than the deposit is worth. When the vault has only one tranche class with supply (e.g. AA-only), the actual accounting in `_virtualPriceAux` assigns **100%** of the epoch gain to that class (`_lastNAV == _lastTrancheNAV`), but `depositDuringEpoch` prices the mint assuming the class earns only `trancheAPRSplitRatio` of the gain. The denominator is therefore too small, the attacker is minted too many tranche tokens, and at epoch end those shares absorb yield that belongs to pre-existing holders.

### Finding Description
Relevant code in `contracts/IdleCDOEpochVariant.sol`:

```solidity
// lines 675-677: only the *depositing* tranche must have supply
uint256 _trancheTotSupply = _trancheSupply(_tranche);
_checkNotAllowed(_trancheTotSupply == 0);

// lines 704-717: existing holders' expected interest uses the split ratio
if (expectedInt > pendingFees) {
  trancheExpected = _calcTrancheInterestShare(
    _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(...)),
    _tranche
  );
}
uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

// line 724: mint priced against expectedFinal
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

`_calcTrancheInterestShare` (lines 883-886) always applies `trancheAPRSplitRatio` / `FULL_ALLOC - trancheAPRSplitRatio`. But the epoch-end gain distribution in `IdleCDOCreditVault._virtualPriceAux` (contracts/IdleCDOCreditVault.sol:319-322) gives the *entire* gain to a class whenever it is the only class with NAV:

```solidity
} else if (_lastNAV == _lastTrancheNAV) {
  _totalTrancheGain = totalGain;
}
```

So in an AA-only (or BB-only) vault, the true end-of-epoch tranche NAV is `lastSavedNAV + netTotalInterest`, while `depositDuringEpoch` mints against `lastSavedNAV + share * netTotalInterest`. With `trancheAPRSplitRatio = r` and total net expected interest `I`, the minted amount is inflated by a factor of roughly `(L + I) / (L + r·I)` for AA-only (and `(L + I) / (L + (1-r)·I)` for BB-only). The extra minted tokens are backed by real NAV at `stopEpoch` and are redeemed at par, siphoning the counterparty-share of interest from existing holders.

No guard prevents this: `depositDuringEpoch` requires only that the *depositing* tranche has nonzero supply; nothing requires both tranches to be live. The flow is gated only by `isDepositDuringEpochDisabled`, `isEpochRunning`, `isAYSActive`, `isProgrammableBorrower` and KYC (`isWalletAllowed`) — an ordinary KYC'd lender qualifies. `_skimDonatedAssets` does not help; the mispricing is not donation-based.

### Impact Explanation
Direct theft of unclaimed yield belonging to existing tranche holders. For an AA-only vault with NAV `L`, net epoch interest `I`, split `r = 0.9`: an attacker depositing amount `a` mints `a·S/(L + 0.9I)` shares instead of `a·S/(L + I)`, capturing roughly `a·I·(1−r)/(L + r·I)` of value that should accrue to existing holders — up to the full `(1−r)·I` share of the epoch's yield as `a/L` grows. For a BB-only vault the theft is larger (the missed share is `r·I`, typically 90%+ of interest). Loss is bounded by the counterparty share of one epoch's net interest, realized in one transaction.

### Likelihood Explanation
Requires: epoch running, `isDepositDuringEpoch` enabled, fixed-APR mode (`isAYSActive == false`), non-programmable borrower, and a single live tranche — a plausible configuration (e.g. a senior-only pool or a junior-only pool before the other tranche is funded). The attacker needs only a KYC'd wallet and capital; everything else is permissionless timing within one running epoch. The bug is deterministic arithmetic, no oracle or privileged misbehavior needed.

### Recommendation
In `depositDuringEpoch`, compute `trancheExpected` and `trancheInterest` using the same waterfall as `_virtualPriceAux`: when the other tranche's saved NAV (or supply) is zero, the depositing tranche should be credited with 100% of net expected interest, not `trancheAPRSplitRatio` of it. Alternatively, revert `depositDuringEpoch` when exactly one of the two tranche classes is live, mirroring the existing `_trancheTotSupply == 0` check. A regression test should deposit mid-epoch into an AA-only vault and assert minted shares equal `_amount` (plus its fair interest) priced against `lastNAVAA + full expected net interest`.

### Proof of Concept
Foundry-style sketch (fork mainnet, using the repo's own test harness conventions from `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testMidEpochDepositOverMintsInSingleTrancheVault() public {
  // AA-only vault: userA seeds the pool, BB tranche has 0 supply / 0 NAV
  idleCDO.depositAA(10_000 * ONE_SCALE);          // userA NAV = L
  vm.prank(manager);
  cdoEpoch.startEpoch();                          // expectedEpochInterest = I over epoch+buffer

  // enable mid-epoch deposits (fixed-APR mode, isAYSActive == false)
  vm.prank(owner);
  cdoEpoch.setIsDepositDuringEpochDisabled(false);

  vm.warp(cdoEpoch.epochEndDate() - cdoEpoch.epochDuration() / 2);

  // attacker (KYC'd lender) deposits `a` mid-epoch
  uint256 a = 10_000 * ONE_SCALE;
  deal(defaultUnderlying, attacker, a);
  vm.startPrank(attacker);
  IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), a);
  uint256 minted = cdoEpoch.depositDuringEpoch(a, address(AAtranche));
  vm.stopPrank();

  // expected: minted should be priced vs lastNAVAA + FULL net interest (BB NAV == 0
  //  => AA receives totalGain in _virtualPriceAux), not lastNAVAA + 0.9 * I.
  // actual: minted = (a + 0.9*I_a) * S / (L + 0.9*I)  -> inflated by ~ (L+I)/(L+0.9I)

  // borrower repays principal + expectedEpochInterest at stopEpoch (honest borrower)
  _repayAndStopEpoch();

  uint256 attackerOut = /* redeem/claim attacker tranche tokens */;
  // attackerOut > a + fairShare(attacker), with the excess taken from userA's yield
  assertGt(attackerOut - a, fairInterestFor(a), "over-minted shares stole unclaimed yield");
}
```

The assertion compares the attacker's redeemed value against a fair mint computed with `expectedFinal = lastNAVAA + netTotalInterest`; the delta equals the attacker's captured share of the interest that `_virtualPriceAux` would have assigned to existing AA holders.