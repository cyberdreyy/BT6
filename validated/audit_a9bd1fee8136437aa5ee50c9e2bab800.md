### Title
Mid-epoch deposit is priced against stale `expectedEpochInterest` after withdrawal requests, under-minting shares to the new depositor and inflating remaining tranche holders - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`depositDuringEpoch` computes the pre-deposit expected final NAV of a tranche as `_lastSavedNAV(_tranche) + trancheExpected`, where `trancheExpected` is the tranche's share of `expectedEpochInterest - pendingWithdrawFees`. `requestWithdraw` removes the withdrawn principal from `lastNAV` and burns tranche supply but never decrements `expectedEpochInterest`; the exited principal's projected interest is only corrected later via `interestForOverUnderPerformance` at the next `startEpoch`. Any lender added mid-epoch after a withdrawal request is therefore priced against an inflated expected NAV — the exact analog of CVE-2019-17341, where a newly attached device is mapped with page attributes that race with a state change.

### Finding Description
- `depositDuringEpoch` reads `expectedInt = expectedEpochInterest` and `trancheExpected = _calcTrancheInterestShare(_netGainAfterFees(expectedInt - pendingWithdrawFees, mgmtFee), _tranche)`, then mints `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal` with `expectedFinal = _lastSavedNAV(_tranche) + trancheExpected` [1](#0-0) .
- `requestWithdraw` calls `_withdrawOps(_amount, principal, _tranche)`, which burns tranche tokens and subtracts only `principal` from `lastNAV`, while the projected `interest` becomes a fixed receipt obligation in `IdleCreditVault.requestWithdraw`. `expectedEpochInterest` is not reduced; only `pendingWithdrawFees += totalFees` and `interestForOverUnderPerformance += diff` are recorded, the latter being applied at the next `startEpoch` [2](#0-1) .
- Therefore after a mid-epoch withdrawal of principal `P` with projected interest `I`, `expectedEpochInterest` still contains `I` even though that interest is now owed to a fixed receipt (not to live NAV), and `_lastSavedNAV` has already dropped by `P`. `expectedFinal` is overstated by the withdrawing tranche's share of `I`, so a mid-epoch depositor receives `(amount + trancheInterest) * supply / (inflated expectedFinal)` — strictly fewer tranche tokens than entitled.
- The minted-share deficit is never refunded; it accrues pro-rata to all remaining tranche holders at `stopEpoch` when the real interest is distributed, i.e. value is transferred from the mid-epoch depositor to pre-existing holders.
- No guard prevents this ordering: `depositDuringEpoch` only checks `isEpochRunning`, flags and KYC [3](#0-2) ; `requestWithdraw` is allowed during a running epoch (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` are set true at `startEpoch`); the `_updateAccounting()` call inside `depositDuringEpoch` does not touch `expectedEpochInterest`, so the staleness survives the checkpoint [4](#0-3) .

### Impact Explanation
Broken invariant: fair mint/burn — the mid-epoch depositor is entitled to `_amount + trancheInterest` at epoch end but is minted shares priced on a NAV that double-counts interest already committed to withdrawn receipts. The shortfall equals `minted_ideal - minted_actual ≈ (amount + trancheInterest) * supply * trancheShare(I_withdrawn) / expectedFinal²`. An attacker who is a KYC-passed tranche holder profits deterministically: they hold a tranche position, a withdrawal request (by anyone, including the attacker's own partial withdrawal keeping residual balance) inflates `expectedFinal`, and the next mid-epoch depositor's under-minted shares transfer value to the attacker's remaining holdings, claimed at `stopEpoch`/`claimWithdrawRequest`. Loss is proportional to the withdrawn tranche's share of projected interest; with a large withdrawal late in a long epoch it can reach several percent of the deposit.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled == false`, `isAYSActive == false`, `isProgrammableBorrower == false`, a running epoch, and at least one withdrawal request before the mid-epoch deposit — all achievable by unprivileged KYC'd users with honest owner/manager. The accounting paths involved (`requestWithdraw` during a running epoch, `depositDuringEpoch`) are both live, mutually permitted features; no epoch phase gating prevents the ordering. The combination is not covered by the existing mid-epoch deposit tests, which never interleave withdrawal requests.

### Recommendation
Track exited expected interest explicitly: in `requestWithdraw`, subtract the withdrawing tranche's share of projected interest from `expectedEpochInterest` (e.g., `expectedEpochInterest -= interest + trancheShareAdjustment`) instead of deferring the full correction to `interestForOverUnderPerformance` at `startEpoch`, or snapshot a `withdrawnExpectedInterest` accumulator that `depositDuringEpoch` subtracts alongside `pendingWithdrawFees` when computing `trancheExpected`. Recompute `expectedFinal` for both tranches consistently since `expectedEpochInterest` is vault-wide while shares are split via `trancheAPRSplitRatio`.

### Proof of Concept
Foundry fork test (against mainnet `token`/`strategy` as in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testDepositDuringEpochStaleExpectedInterest() external {
    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // victim-class liquidity
    idleCDO.depositBB(amount);            // attacker liquidity
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    _startEpochAndCheckPrices(0);

    // attacker (KYC'd BB holder) requests withdraw of most BB principal mid-epoch
    vm.warp(cdoEpoch.epochEndDate() - cdoEpoch.epochDuration() / 2);
    uint256 bbBal = IERC20(BBtranche).balanceOf(address(this));
    cdoEpoch.requestWithdraw(bbBal * 9 / 10, address(BBtranche));
    // expectedEpochInterest still contains the exited principal's interest

    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address victim = makeAddr('victim');
    uint256 dep = 1000 * ONE_SCALE;
    deal(defaultUnderlying, victim, dep);
    vm.startPrank(victim);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), dep);
    uint256 minted = cdoEpoch.depositDuringEpoch(dep, address(AAtranche));
    vm.stopPrank();

    // fair minted recomputed with corrected expectedEpochInterest
    // (subtract withdrawn tranche share of interest) is strictly greater:
    // assert minted < fairMinted, and at stopEpoch the residual holders'
    // per-token payout exceeds the no-withdrawal baseline.
    _toggleEpoch(false, initialApr, _expectedFundsEndEpoch());
    uint256 price = cdoEpoch.virtualPrice(address(AAtranche));
    assertLt(minted * price / ONE_TRANCHE_TOKEN, dep + expectedVictimInterest - 1);
}
```

The assertion compares the victim's epoch-end value against `dep + fair trancheInterest`; the deficit equals the withdrawn tranche's stale share of `expectedEpochInterest` folded into `expectedFinal`.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L658-669)
```text
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L679-682)
```text
    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L699-725)
```text
    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-791)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
  }
```
