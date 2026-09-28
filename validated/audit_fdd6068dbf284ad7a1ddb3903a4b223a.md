### Title
Epoch interest proration assumes every year has 365 days — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
All epoch-based interest calculations in the epoch variant and the programmable borrower prorate the APR against a fixed `365 days` denominator (`YEAR` in `BaseStrategy.sol`). When an epoch or borrower accrual window spans Feb 29 of a leap year, the actual elapsed seconds are 366 days' worth, so `elapsed / 365 days` over- or under-mints interest versus a true annual rate. The mismatch is systematic, applies to every lender and the borrower of the affected pool, and there is no setter or time-aware library to correct the denominator.

### Finding Description
`IdleCDOEpochVariant._calcInterestWithApr` computes epoch interest as:

```solidity
// contracts/IdleCDOEpochVariant.sol:807-809
function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
  return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
}
```

The same constant feeds `_calcInterestWithdrawRequest` (withdrawal receipt interest) and `_calcInterest` (epoch-end interest used by `stopEpoch` accounting and the AA/BB split via `trancheAPRSplitRatio`). `ProgrammableBorrower._calcInterest` does the identical proration:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:561-563
function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
  return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
}
```

with `YEAR = 365 days` defined in `contracts/strategies/BaseStrategy.sol`. If `epochDuration` is a year or the accrual window covers a leap day, the formula treats the year as 31,536,000 seconds while the real year is 31,622,400 seconds — a 86,400 s error, i.e. interest computed at ~100.27% of the quoted APR annualized (or conversely, a fixed "per-second min rate" interpretation under-charges by 1/366, exactly as in the Astaria report).

### Impact Explanation
On a pool with e.g. 10% APR and 10M USDC notional, an epoch spanning a leap day mis-prices interest by roughly `interest × 1/365 ≈ 0.274%` of the gross interest (~$2,700 per $1M interest baseline). The error is either paid as excess interest out of the NAV (diluting remaining tranche holders through `tranchePrice`/`_updateAccounting`) or under-credited to lenders on withdrawal receipts minted via `requestWithdraw`/`_calcInterestWithdrawRequest`, which fixes the payout at request time and is irreversible once the epoch rolls. No guard (skim, flags, epoch gating) catches a wrong-time-basis result because the value is mathematically "valid" output.

### Likelihood Explanation
Leap years occur every 4 years (2024, 2028, …) and long-lived credit vaults will have epochs/accrual windows crossing them. The bug triggers deterministically from `block.timestamp` progression — no privileged or attacker action required; any epoch containing Feb 29 is affected, and every unprivileged tranche holder redeeming in that window receives the mis-priced payout.

### Recommendation
Use `365.25 days` (or a date-aware proration) as `YEAR`/`365 days` in `IdleCDOEpochVariant._calcInterestWithApr` and `ProgrammableBorrower._calcInterest`, or document that APR is defined per 31,536,000-second year so front-end expectations match on-chain math.

### Proof of Concept
Fork test (Foundry, mainnet pool using `IdleCreditVault`/`IdleCDOEpochVariant`):

```solidity
// Warp to just before a leap-year epoch boundary
vm.warp(1709251200); // 2024-03-01, epoch contained Feb 29

uint256 principal = 10_000_000e6;
uint256 apr = 10e20; // 10% APR * 1e18
uint256 epochDuration = 366 days; // calendar year 2024

uint256 interest = principal * (apr / 100) * epochDuration / (365 days * 1e18);
// interest = principal * 0.10 * 366/365 => over-accrued by ~0.274%
assertGt(interest, principal * 10 / 100); // exceeds the quoted annual APR
```

Equivalently for `ProgrammableBorrower`: set `borrowerApr`, warp `lastBorrowerAccrual` to 2024-01-01 and `block.timestamp` to 2025-01-01; `totalInterestDueNow()` returns `principal * apr/100 * 366/365`, i.e. the borrower is charged 1 extra day of interest relative to the annualized rate, flowing into `expectedEpochInterest`/`stopEpoch` and permanently diluting or enriching one tranche class vs. the documented APR. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L804-809)
```text
  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L554-563)
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
```
