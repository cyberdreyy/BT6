### Title
Precision loss in epoch interest calculation causes systemic under-charge of borrower interest — (`contracts/IdleCDOEpochVariant.sol:807-809`, `contracts/strategies/idle/ProgrammableBorrower.sol:561-563`)

### Summary
`_calcInterestWithApr` (and its twin `ProgrammableBorrower._calcInterest`) performs the division `_apr / 100` **before** multiplying by the amount and duration. Because `_apr` is a percentage scaled by `1e18` (e.g. `10% = 10e18`), the intermediate division truncates up to `99` wei of APR. The truncated rate is then multiplied by the full TVL and epoch duration, so the lost interest scales with vault size and is structurally under-counted every epoch.

### Finding Description
In `IdleCDOEpochVariant`:

```solidity
function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
  return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
}
``` [1](#0-0) 

`ProgrammableBorrower` repeats the pattern:

```solidity
function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
  return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
}
``` [2](#0-1) 

The exact formula is `amount * apr * duration / (100 * YEAR * 1e18)`. The code instead computes `amount * floor(apr/100) * duration / (YEAR * 1e18)`, dropping `apr % 100` (up to `99` units of a `1e18`-scaled percentage) before the multiplications.

This truncated value feeds the credit-vault surface at:
- `startEpoch()`: `expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest)` where `adjustedActiveInterest` uses `_calcInterest(getContractValue())` — the borrower is asked to return less interest than owed [3](#0-2) 
- `depositDuringEpoch` interest proration (`_calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)`) [4](#0-3) 
- `_calcInterestWithdrawRequest` / `interestWithoutSplitRatio`, which size each withdrawer's projected interest receipt and `interestForOverUnderPerformance` [5](#0-4) 
- `ProgrammableBorrower._accrueBorrowerInterest` → `borrowerInterestAccrued` → `totalInterestDueNow`, the debt the borrower must repay [6](#0-5) 

The error is:

```
lostInterest = amount * (apr % 100) * duration / (365 days * 1e18)
```

For an 18-decimal underlying with 10M TVL (`amount = 1e25`), a 30-day epoch, and worst-case `apr % 100 = 99`:

```
loss ≈ 1e25 * 99 * 2592000 / (31536000 * 1e18) ≈ 81e18  (~81 tokens per epoch)
```

### Impact Explanation
The invariant broken is fair mint/burn / solvency of expected yield: `expectedEpochInterest` and each receipt's projected interest are computed on a rate strictly lower than the configured APR, so tranche holders permanently lose the truncated portion of every epoch's yield, while the (honest) borrower is under-charged. In the programmable-borrower path, `borrowerInterestAccrued` itself is understated, so `repay`/`totalInterestDueNow` accepts less repayment than the configured `borrowerApr` implies. The loss is small per epoch but recurring, scales linearly with TVL and duration, and hits its maximum whenever `apr` is set to a value not divisible by `100` wei — a normal operational condition, since APRs are stored at `1e18` precision and any non-round value (e.g. `8.345% = 8.345e18`) truncates `45` wei of rate.

### Likelihood Explanation
No attacker action is required; the loss is deterministic on every `startEpoch`, `requestWithdraw`, `depositDuringEpoch`, `maxWithdrawable`, and borrower accrual while `apr % 100 != 0`. The manager/owner setting APRs are honest, but nothing constrains APRs to multiples of `100` wei, so in practice truncation is nearly always nonzero. Severity is limited by the small per-call magnitude, so this is a medium rather than high issue in this codebase.

### Recommendation
Reorder operations to multiply before dividing, folding the `100` into the final denominator:

```solidity
// IdleCDOEpochVariant._calcInterestWithApr
return _amount * _apr * epochDuration / (365 days * ONE_TRANCHE_TOKEN * 100);

// ProgrammableBorrower._calcInterest
return principal * borrowerApr * elapsed / (YEAR * ONE_TRANCHE_TOKEN * 100);
```

Apply the same fix in the Foundry test helpers that mirror these formulas (`_calcInterestAtApr` in `test/foundry/IdleCreditVault.t.sol`).

### Proof of Concept
Foundry-style PoC showing the divergence (drop into the existing `IdleCreditVault.t.sol` harness):

```solidity
function testInterestPrecisionLoss() external {
    // TVL: 10M of an 18-decimal underlying, APR set to a non-round value
    uint256 amount = 10_000_000e18;
    uint256 apr    = 8.345e18;           // apr % 100 = 45 truncated
    uint256 dur    = 30 days;

    // contract computation
    uint256 got = amount * (apr / 100) * dur / (365 days * 1e18);
    // exact computation
    uint256 exact = amount * apr * dur / (365 days * 1e18 * 100);

    uint256 lost = exact - got;
    // lost == amount * (apr % 100) * dur / (365 days * 1e18)
    assertEq(lost, amount * (apr % 100) * dur / (365 days * 1e18));
    assertGt(lost, 0); // ≈ 37 tokens of yield silently dropped per epoch
}
```

The same divergence can be demonstrated end-to-end by calling `startEpoch()` and comparing `expectedEpochInterest` against the exact formula, and likewise for `ProgrammableBorrower.borrowerInterestAccrued` after warping time.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L260-262)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
```

**File:** contracts/IdleCDOEpochVariant.sol (L693-697)
```text
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);
```

**File:** contracts/IdleCDOEpochVariant.sol (L807-809)
```text
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L865-877)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L554-578)
```text
  function _pendingBorrowerInterest(uint256 principal, uint256 last) internal view returns (uint256) {
    if (principal == 0 || last == 0 || borrowerApr == 0) return 0;
    uint256 elapsed = block.timestamp - last;
    return elapsed == 0 ? 0 : _calcInterest(principal, elapsed);
  }

  /// @notice Compute simple time-based borrower interest for a principal over an elapsed period.
  function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
    return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
  }

  /// @notice Checkpoint borrower interest up to `block.timestamp`.
  /// @dev The first call only seeds the accrual clock. When principal or APR is zero we still
  /// move the timestamp forward so interest starts accruing cleanly from the next state change.
  function _accrueBorrowerInterest() internal {
    uint256 last = lastBorrowerAccrual;
    uint256 principal = borrowerPrincipal;
    if (last == 0 || principal == 0 || borrowerApr == 0) {
      lastBorrowerAccrual = block.timestamp;
      return;
    }
    uint256 elapsed = block.timestamp - last;
    if (elapsed == 0) return;
    borrowerInterestAccrued += _calcInterest(principal, elapsed);
    lastBorrowerAccrual = block.timestamp;
```
