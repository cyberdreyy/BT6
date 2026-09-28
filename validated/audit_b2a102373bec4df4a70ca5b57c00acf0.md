### Title
`setEpochParams` changes `epochDuration`/`bufferPeriod` without rescaling the stored strategy APR, letting withdrawers claim mispriced interest - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary

Analogous to M-13 (where `setIntervals` updates `tuneIntervalCapacity` but leaves the dependent `tuneBelowCapacity` stale), `IdleCDOEpochVariant.setEpochParams` updates `epochDuration` and `bufferPeriod` but never rescales the APR stored in `IdleCreditVault`. The strategy APR is a *derived* value: it is set via `_setScaledApr(_newApr)`, which calls `IdleCreditVault(strategy).setAprsWithBuffer(_newApr, epochDuration, bufferPeriod)` [1](#0-0) . After a legitimate `setEpochParams` call, the scaled APR still embeds the old duration/buffer ratio, while `_calcInterest` and `_calcInterestWithdrawRequest` multiply it by the *new* `epochDuration` [2](#0-1) . The code itself documents the hazard: "bufferPeriod should not be changed once set otherwise interest calculations will be wrong" [3](#0-2) , but the same stale-scaling applies to `epochDuration` changes and there is no guard enforcing the warning.

### Finding Description

`_setScaledApr` is invoked only at initialization, inside `stopEpoch`/`stopEpochWithDuration`, and in `finalizeDefault` [4](#0-3) . `setEpochParams` (callable by owner or manager whenever no epoch is running and the pool is not closed) writes `epochDuration` and `bufferPeriod` directly and returns without touching the strategy [5](#0-4) .

The scaled APR stored in the vault equals `unscaledApr * (epochDuration + bufferPeriod) / epochDuration` so that interest covers the buffer. Withdraw-request pricing then de-scales it: `interest = _calcInterest(amount) * _duration / (_duration + _buffer)` using the **new** durations [6](#0-5) . After a duration/buffer change the de-scaling factor no longer matches the stored scaling factor, so the interest credited to a withdraw request (and the `diff`/`interestForOverUnderPerformance` term that shifts yield between AA and BB [7](#0-6) ) is systematically wrong. An unprivileged, KYC-passed lender can call `requestWithdraw` during the buffer window between `setEpochParams` and the next `startEpoch`/`stopEpoch` (the only calls that refresh the scaled APR) and lock in a receipt paying more (or less) than the correct interest.

The same staleness corrupts `expectedEpochInterest` in `startEpoch` (computed via `_calcInterest(getContractValue())` [8](#0-7) ) and the mid-epoch deposit mint formula in `depositDuringEpoch` [9](#0-8)  — every consumer of `_calcInterest` reads a stale derived parameter, exactly mirroring the M-13 pattern of a setter leaving a dependent value outdated.

### Impact Explanation

Broken invariant: fair mint/burn / solvency of the receipt queue. If `epochDuration` is increased (or `bufferPeriod` decreased) without APR rescaling, the effective per-epoch interest embedded in `requestWithdraw` receipts exceeds the borrower's true obligation; the excess is paid out of vault NAV at `claimWithdrawRequest`, i.e. transferred from remaining LPs to the withdrawing attacker. In the opposite direction, `expectedEpochInterest` under-charges the borrower, producing an accounting shortfall socialized across tranche holders at `stopEpoch`. Both directions are direct, quantifiable value transfers scaled by `|apr·(Δduration/duration)|` on the requested amount.

### Likelihood Explanation

- `setEpochParams` is a normal governance operation (adjusting epoch cadence between epochs); the rules explicitly permit sequencing an unprivileged attack around honest owner/manager calls.
- No guard blocks it: `setEpochParams` reverts only on `defaulted`, `isEpochRunning`, or zero durations [10](#0-9)  — the stale-scaled-APR state is fully reachable and persists for the entire buffer window, during which `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are open.
- Exploitation requires only a KYC-passed lender calling `requestWithdraw`, which `_skimDonatedAssets`, `_updateAccounting`, and KYC checks do not prevent.

### Recommendation

In `setEpochParams`, call `_setScaledApr(IdleCreditVault(strategy).unscaledApr())` after updating `epochDuration`/`bufferPeriod` so the stored scaled APR always reflects the current durations — the direct analog of the M-13 fix (recomputing `tuneBelowCapacity` inside `setIntervals`). Alternatively, enforce the documented invariant by reverting when `bufferPeriod` (or the duration/buffer ratio) changes while a nonzero APR is set.

### Proof of Concept

Foundry fork PoC (extends `test/foundry/IdleCreditVault.t.sol` setup where `cdoEpoch`, `idleCDO`, `AAtranche`, `manager`, `owner` are wired):

```solidity
function testStaleScaledAprAfterSetEpochParams() external {
    uint256 amount = 100_000 * ONE_SCALE;
    // Setup: AA deposit, one epoch runs and stops so lastEpochApr/scaled apr are set
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0); // scaled apr = apr*(dur+buffer)/dur

    // Honest manager doubles epoch duration; scaled apr in strategy is NOT rescaled
    vm.prank(manager);
    cdoEpoch.setEpochParams(2 * cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    // Attacker (KYC-passed lender) requests withdraw; interest is computed with
    // stale scaled apr * new duration -> receipt is overpriced ~2x the buffer-scaled interest
    uint256 bal = underlying.balanceOf(attacker);
    vm.prank(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Compare against correctly-priced interest (apr applied to the true duration)
    uint256 correctInterest = amount * (initialProvidedApr / 100) * cdoEpoch.epochDuration() / (365 days * ONE_TRANCHE_TOKEN);
    assertGt(requested, amount + correctInterest, 'receipt overpriced by stale scaled apr');

    // After next epoch settles, attacker claims the inflated receipt, paid from vault NAV
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - bal, amount + correctInterest);
}
```

The asymmetric case (`bufferPeriod` reduced) analogously under-charges `expectedEpochInterest` at `startEpoch`, leaving the vault short of borrower funds at `stopEpoch`.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L113-125)
```text
  /// @notice update epoch duration
  /// @dev IMPORTANT: bufferPeriod should not be changed once set otherwise interest calculations will be wrong
  /// @param _epochDuration duration in seconds
  /// @param _bufferPeriod time between 2 epochs
  function setEpochParams(uint256 _epochDuration, uint256 _bufferPeriod) public {
    _checkOnlyOwnerOrManager();
    // cannot set epoch params if epoch is running
    // cannot set epochDuration to 0 as it's reserved for closing the pool
    // and cannot set epochDuration if previously was set to 0 as borrower repaid all funds
    _checkNotAllowed(defaulted || isEpochRunning || _epochDuration == 0 || epochDuration == 0);
    epochDuration = _epochDuration;
    bufferPeriod = _bufferPeriod;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L260-262)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-529)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
```

**File:** contracts/IdleCDOEpochVariant.sol (L544-546)
```text
  function _setScaledApr(uint256 _newApr) internal {
    IdleCreditVault(strategy).setAprsWithBuffer(_newApr, epochDuration, bufferPeriod);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L691-724)
```text
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);

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
```

**File:** contracts/IdleCDOEpochVariant.sol (L780-784)
```text
    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;
```

**File:** contracts/IdleCDOEpochVariant.sol (L807-809)
```text
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L862-877)
```text
    uint256 _buffer = bufferPeriod;
    // calculate total vault interest (they don't get the interest for the buffer period for withdraw requests so 
    // we scale it back since _calcInterest is scaling the interest with tht buffer period),
    uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
    // calculate total tranche interest for the whole tranche supply
    uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
    // calculate interest for the given tranche and given amount
    uint256 _trancheBal = _lastSavedNAV(_tranche);
    _interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
    // calculate the interest that the _amount would have received if there was no split ratio (ie interest split based only on tvl).
    // This is used to calculate the interest that should be added to the expectedEpochInterest when 
    // withdrawing an AA tranche or the interest that should be removed from expectedEpochInterest when
    // withdrawing a BB tranche
    uint256 interestWithoutSplitRatio = _calcInterest(_amount) * _duration / (_duration + _buffer);
    // difference between total interest and tranche interest (positive for AA, negative for BB)
    _diff = int256(interestWithoutSplitRatio) - int256(_interest);
```
