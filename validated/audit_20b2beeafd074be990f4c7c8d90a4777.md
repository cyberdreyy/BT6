### Title
Borrower interest is truncated to zero on frequent accrual checkpoints for low-decimal underlyings - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` accrues contractual borrower interest in discrete checkpoints: `_accrueBorrowerInterest()` computes `principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN)` and then unconditionally moves `lastBorrowerAccrual` forward to `block.timestamp`. When the underlying token has few decimals and `elapsed` is small, the integer division truncates the per-checkpoint interest to `0`, and because the timestamp is still updated, that fractional interest is permanently lost. This is the same bug class as the Teller report: time-based interest divided by a year denominator rounding to 0 for low-precision tokens — but here it is worse, because the accrual clock is reset on every call, so the loss is cumulative and unbounded.

### Finding Description [1](#0-0) [2](#0-1) 

`_accrueBorrowerInterest` is invoked on every state-changing call that touches the borrower ledger: `borrow`/`executeBorrow`, `repay`/`executeRepay`, `setBorrowerApr`, `onStartEpoch`, and `onStopEpoch`. Each call:

1. computes `borrowerInterestAccrued += _calcInterest(principal, elapsed)` where `_calcInterest` divides by `YEAR * 1e18` (`= 3.1536e25`);
2. sets `lastBorrowerAccrual = block.timestamp`, discarding the remainder.

Truncation condition: interest for an interval is 0 whenever `principal * (borrowerApr/100) * elapsed < 3.1536e25`.

Concrete example (2-decimal stablecoin like GUSD as `underlyingToken`):
- `borrowerPrincipal = 1_000_000` units (= $10,000), `borrowerApr = 10e18` (10%) → `borrowerApr/100 = 1e17`.
- Numerator per second: `1e6 * 1e17 = 1e23`. Truncates to 0 for any `elapsed < ~315 s`.
- If accrual is checkpointed more often than every ~5 minutes (a keeper/executor bot calling `executeRepay`/`executeBorrow` each block, or frequent `repay` calls by the honest borrower), **every** checkpoint yields 0 and the clock is still advanced — 100% of contractual borrower interest (~$1,000/yr on $10k at 10%) is silently dropped. With USDC (6 decimals) the full-loss threshold is ~`elapsed < 3.15e8 / principal` seconds; for a $100 principal position even once-per-5-minute accrual truncates everything.

The lost interest flows directly into `borrowerInterestAccruedNow()` → `borrowerInterestOwedNow()` → `totalInterestDueNow()`, which is the single value `IdleCDOEpochVariant` reads at `stopEpoch` to price the epoch. Truncated accrual therefore under-reports pool interest, so tranche NAV/`expectedEpochInterest` is short and lenders receive less than the contractual APR, while the borrower's debt recorded in `borrowerInterestDebt`/`borrowerInterestAccrued` is correspondingly smaller — the borrower underpays and there is no recovery path. Note `_pendingBorrowerInterest` (the view path) has the same formula, so even between checkpoints the sub-1-unit fractional accrual is never captured.

### Impact Explanation
Permanent loss of yield for tranche holders: the borrower pays strictly less than the contractual `borrowerApr`, with the shortfall equal to the sum of all truncated remainders — up to the entire interest amount when checkpoints are frequent relative to `principal * apr`. Loss scales inversely with token decimals (worst for 2-decimal stablecoins, still present for USDC/USDT). No privileged misbehavior is required; it manifests through routine honest borrower repayments, executor keepers, or epoch hook calls.

### Likelihood Explanation
Medium-high for low-decimal underlyings. `repay(0)` repays the full tracked obligation, and a borrower (or its authorized executor) doing periodic partial repayments, or frequent `borrow`/`repay` cycling of a revolving facility, triggers `_accrueBorrowerInterest` each time. Deployments denominated in 2–6 decimal stablecoins with modest facility sizes hit the truncation regime under ordinary operation.

### Recommendation
Accumulate the remainder instead of discarding it: keep a scaled accumulator (e.g., store interest in `1e18`-scaled fixed point and only convert to token units at read/settlement), or do not advance `lastBorrowerAccrual` by the full `elapsed` — advance it only by the time actually paid for: `lastBorrowerAccrual += accrued * YEAR * ONE_TRANCHE_TOKEN / (principal * (borrowerApr/100))`. Alternatively compute interest lazily from a fixed per-epoch start timestamp rather than checkpointing on every call.

### Proof of Concept
Foundry-style PoC sketch (against the existing `ProgrammableBorrower` harness in `test/foundry/ProgrammableBorrowerCreditVault.t.sol` / `ProgrammableBorrowerAccountingInvariant.t.sol`):

```solidity
// underlying: MockInvariantERC20 with decimals() == 2
// borrowerApr = 10e18 (10%), borrowerPrincipal drawn = 1_000_000 units ($10k)

// epoch running; borrower drew 1e6 units
for (uint256 i; i < 100; ++i) {
    vm.warp(block.timestamp + 60);           // 60s < ~315s truncation threshold
    vm.prank(borrower);
    borrowerContract.repay(1);               // any repay triggers _accrueBorrowerInterest
}

// 100 minutes elapsed: continuous interest should be
// 1e6 * 1e17 * 6000 / (365 days * 1e18) ≈ 19 units
assertEq(borrowerContract.borrowerInterestAccrued(), 0);      // all truncated
assertEq(borrowerContract.borrowerInterestAccruedNow(), 0);   // view agrees
// every checkpoint advanced lastBorrowerAccrual despite accruing 0
```

Expected vs actual: continuous accrual over the same window yields ~19 units, while checkpointed accrual yields 0 — the interest is permanently lost and `totalInterestDueNow()` under-reports the epoch yield to `IdleCDOEpochVariant`.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L561-563)
```text
  function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
    return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L568-579)
```text
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
  }
```
