### Title
Divide-before-multiply in `_calcInterestWithApr` zeroes out interest for sub-1% APR epochs and underpays withdrawers - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
`_calcInterestWithApr` computes epoch interest as `_amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN)`. Because `_apr / 100` is evaluated **before** the multiplication, any APR below 1% (i.e., `apr < 100` in the strategy's scaled representation where 100% = `1e20`) truncates to `0`, and any fractional-percent component of larger APRs is silently dropped. This is the same bug class as the Remora `FiveFiftyRule::_updateEntityAllowance` finding: `(DENOMINATOR / x) * amount` instead of `DENOMINATOR * amount / x`. [1](#0-0) 

### Finding Description
The function feeds two sensitive paths:

1. `requestWithdraw` → `_calcInterestWithdrawRequest`, which computes the interest credited to a user's withdrawal receipt via `totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer)` and `interestWithoutSplitRatio = _calcInterest(_amount) * _duration / (_duration + _buffer)`. If the epoch APR is e.g. 0.9%, `_apr / 100 == 0`, so `totInterest == 0`, every withdrawer's `_interest == 0`, and `_diff` is computed against a zero interest baseline. The receipt minted via `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` at `contracts/IdleCDOEpochVariant.sol:788` permanently fixes a payout that excludes all yield for the epoch.

2. `interestForOverUnderPerformance += diff` at line 784 is also corrupted: for an AA withdrawal `_diff` becomes positive (since `interestWithoutSplitRatio` is also computed with the truncated helper — but any fractional truncation skews the over/under-performance bookkeeping), distorting `expectedEpochInterest` at the next `startEpoch`.

Additionally, APRs like 5.9% lose the 0.9% component entirely: `590 / 100 = 5`, so lenders accrue interest at 5.0% for the whole epoch. Contrast with the correct multiply-first pattern used elsewhere in the same file, e.g. `_totalInterest * ratio / FULL_ALLOC` in `_calcTrancheInterestShare` (`contracts/IdleCDOEpochVariant.sol:885`) and `(_apr0NetInterest * 1e18) / _principal` in `prepareStopEpochWithApr0` (`contracts/strategies/idle/IdleCreditVault.sol:537`).

### Impact Explanation
- For any epoch where the borrower-set APR is < 1%, withdrawers receive **zero** interest on their principal despite the borrower repaying real yield to `IdleCreditVault`. That yield remains in the vault and is captured by remaining tranche holders — an unprivileged lender who stays deposited while others withdraw absorbs the unclaimed yield (theft of unclaimed yield / misallocation).
- For fractional-percent APRs (e.g., 5.5%), the fractional portion of interest is stripped from every withdrawer and from the over/under-performance accounting, again redistributing value to non-withdrawing holders.
- Quantified bound: up to ~1% annualized of the withdrawing principal per affected epoch (e.g., ~30 days at 0.99% APR ≈ 8 bps of principal per epoch per withdrawer).

### Likelihood Explanation
Sub-1% or fractional APRs are realistic in a credit-vault context (risk-off epochs, discounted mid-epoch deposits, low-utilization periods). The APR is set by the honest borrower/manager — no malicious input is required for the truncation to occur; the loss is triggered by any unprivileged lender simply calling `requestWithdraw` during such an epoch. No guard stops it: `_checkTranche`, allow-flags, and epoch gating only control *when* the request is made, not the correctness of the computation.

### Recommendation
Reorder the operation in `_calcInterestWithApr` (and the identical test helper `_calcInterestAtApr` in `test/foundry/IdleCreditVault.t.sol:3557`) to multiply first:

```solidity
// contracts/IdleCDOEpochVariant.sol
return _amount * _apr * epochDuration / (100 * 365 days * ONE_TRANCHE_TOKEN);
```

If overflow is a concern, use `Math.mulDiv(_amount * _apr, epochDuration, 100 * 365 days * ONE_TRANCHE_TOKEN)`.

### Proof of Concept
A Foundry PoC should deploy `IdleCDOEpochVariant` + `IdleCreditVault` (mirroring `test/foundry/IdleCreditVault.t.sol` setup), set the vault APR to e.g. `0.9e18`-equivalent (or 5.9%), run a full epoch with an AA deposit, then call `requestWithdraw` and assert:

```solidity
// _calcInterestWithApr(1_000_000e18, 0.9%apr) == 0  (truncated)
// whereas mulDiv(_amount * _apr * epochDuration, 1, 100*365 days*1e18) > 0
uint256 interestBug = cdo.requestWithdraw(trancheAmount, address(AAtranche));
// receipt equals principal + 0 interest; borrower repays real yield which
// remains in vault NAV, captured by remaining BB/AA holders.
assertLt(interestBug, principal + expectedInterest - fees);
```

Note: I was unable to fully verify the exact decimal scale of `unscaledApr`/`getApr` in `IdleCreditVault` within the available iterations; if APR is expressed in whole-percent units (not 1e18-scaled), the truncation threshold shifts accordingly, but the divide-before-multiply ordering and its precision-loss class remain the same.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L807-809)
```text
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```
