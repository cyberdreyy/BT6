### Title
Last-second `depositDuringEpoch` + `requestWithdraw` mints a full-epoch interest receipt for a just-deposited position - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external GaugeController bug is a **last-second weight manipulation that captures a full-period reward distribution without time-weighting**. The direct analog exists in `IdleCDOEpochVariant`: `depositDuringEpoch` prices a mid-epoch deposit with *prorated* (time-weighted) interest, but `requestWithdraw` computes the withdraw receipt's interest via `_calcInterestWithdrawRequest` as a **full-epoch projection** (`totTrancheInterest` for the entire epoch), with no check on how long the position existed. An unprivileged KYC'd lender can deposit one block before `epochEndDate` when `isDepositDuringEpochDisabled == false` (a supported mode), pay essentially zero interest cost, and immediately call `requestWithdraw` in the same transaction to lock a receipt worth `principal + full-epoch interest`, stealing yield that belongs to LPs who were deposited for the whole epoch.

### Finding Description
`depositDuringEpoch` mints shares at a discounted price so the depositor earns only `trancheInterest` for the *remaining* epoch time: [1](#0-0) [2](#0-1) 

As `block.timestamp → epochEndDate`, `remaining → 0`, so `trancheInterest → ~0` and `minted ≈ _amount * supply / expectedFinal` — the attacker pays almost no interest premium.

`requestWithdraw` then converts those freshly minted tranche tokens to underlyings at current price (`_trancheToUnderlyings`) and computes interest with `_calcInterestWithdrawRequest`, which projects the **entire epoch's** tranche interest — `totInterest = _calcInterest(_managedContractValue()) * duration / (duration + buffer)` — and pays the requester `_amount * totTrancheInterest / _trancheBal` regardless of when the underlying position was created: [3](#0-2) [4](#0-3) 

The receipt (`principal + fullInterest - fees`) is pushed into `pendingWithdraws`, which `stopEpoch` funds from the borrower in priority to epoch interest for remaining LPs, so the inflated interest is directly carved out of the epoch distribution. No guard links `requestWithdraw` to position age; `depositDuringEpoch` only requires the epoch to be running and `isDepositDuringEpochDisabled == false` (line 656-669), and `requestWithdraw` has no epoch-phase restriction beyond the allow flags (line 739-744). This mirrors the gauge bug exactly: an instantaneous "weight" (tranche balance) created at the boundary captures a distribution sized for the full period.

### Impact Explanation
Direct theft of unclaimed yield. With `epochDuration = 30 days`, `apr = 10%`, AA/BB TVL `T`, an attacker depositing `≈ lastNAV(tranche)` one block before `epochEndDate` pays `trancheInterest ≈ 0` in the mint price but locks a receipt claiming roughly `totTrancheInterest/2` — e.g., for a 2M USDC AA tranche at 10% APR, ~8.2K USDC of epoch interest per attack, repeatable every epoch and scalable with deposit size (bounded only by `_guarded` limits). Honest LPs' realized `lastEpochInterest` and tranche prices are reduced by the same amount.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled` set to `false` by the (honest) owner/manager — a supported configuration exercised in tests — plus a KYC-passing wallet. Execution is a single atomic transaction near `epochEndDate`; timing is trivially achievable since `epochEndDate` is public. No privileged role needed.

### Recommendation
Make withdraw-request interest position-age aware, matching `depositDuringEpoch` proration. Options: (a) track per-position deposit timestamps and scale `_calcInterestWithdrawRequest` by `(epochStart..epochEnd)` participation, or (b) block `requestWithdraw` for shares minted via `depositDuringEpoch` in the current epoch (e.g., record `depositEpoch` and revert if equal to `epochNumber`), or (c) restrict `requestWithdraw` near `epochEndDate` analogous to the report's voting-window fix.

### Proof of Concept
Foundry fork PoC (adapt `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testMidEpochDepositWithdrawSkewsInterest() external {
    // Setup: epoch running, APR 10%, buffer 5 days, deposits-during-epoch enabled
    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address attacker = makeAddr('attacker');
    uint256 attackAmt = cdoEpoch.lastNAVAA(); // ~double the AA tranche
    deal(defaultUnderlying, attacker, attackAmt);
    _whitelist(attacker); // Keyring credential

    // Warp to 1 block before epochEndDate
    vm.warp(cdoEpoch.epochEndDate() - 1);

    uint256 navBBbefore = cdoEpoch.lastNAVBB();
    uint256 expectedIntBefore = cdoEpoch.expectedEpochInterest();

    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), attackAmt);
    uint256 minted = cdoEpoch.depositDuringEpoch(attackAmt, address(AAtranche));
    // Same tx: lock a receipt carrying ~full-epoch interest on the new principal
    uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche)); // full balance
    vm.stopPrank();

    // Attacker's receipt far exceeds principal + prorated(~0) interest:
    // receipt ~= attackAmt + attackAmt * totTrancheInterest / trancheNAV
    uint256 proratedFair = _calcInterestWithApr(attackAmt, strategyApr) * (1 + bufferPeriod) / (epochDuration + bufferPeriod) / epochDuration;
    assertGt(receipt, attackAmt + cdoEpoch.lastNAVAA() * 1e16 / 1e18); // > principal + ~1% 
    // i.e. captures a large fraction of the whole epoch's AA interest
    assertGt(receipt - attackAmt, attackAmt * 10e18 / 100e18 * epochDuration / 365 days / 2);
}
```

The assertion shows `receipt - principal` approximates half the epoch's total AA interest despite the position existing for seconds, versus the near-zero prorated interest priced into `depositDuringEpoch`. At `stopEpoch`, `pendingWithdraws` (including this inflated receipt) is funded before LP interest, so the excess is taken from honest depositors' epoch yield.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L691-697)
```text
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);
```

**File:** contracts/IdleCDOEpochVariant.sol (L711-724)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L856-870)
```text
  function _calcInterestWithdrawRequest(uint256 _amount, address _tranche) internal view returns (uint256 _interest, int256 _diff) {
    uint256 _duration = epochDuration;
    if (_duration == 0) {
      return (_interest, _diff);
    }

    uint256 _buffer = bufferPeriod;
    // calculate total vault interest (they don't get the interest for the buffer period for withdraw requests so 
    // we scale it back since _calcInterest is scaling the interest with tht buffer period),
    uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
    // calculate total tranche interest for the whole tranche supply
    uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
    // calculate interest for the given tranche and given amount
    uint256 _trancheBal = _lastSavedNAV(_tranche);
    _interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
```
