### Title
Truncating division on APR before multiplication zeroes/under-counts epoch interest — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant._calcInterestWithApr` computes interest as `_amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN)` at `contracts/IdleCDOEpochVariant.sol:807-809`. Because `_apr / 100` is evaluated first, any APR value not an exact multiple of 100 is truncated down, and any APR below 100 (in the vault's APR units) collapses to zero. This is the same "multiplication on the result of a division" bug class as the external report: the truncated quotient is then multiplied by the amount and epoch duration, amplifying a small rounding error into a material mispricing of interest.

### Finding Description
`_calcInterestWithApr` is the root interest primitive of the credit-vault epoch system. It feeds:

- `_calcInterest` (`IdleCDOEpochVariant.sol:800-802`), which reads the live strategy APR via `_getStrategyApr()`.
- `_calcInterestWithdrawRequest` (`IdleCDOEpochVariant.sol:856-878`), which computes both the interest paid on a withdrawal receipt (`_interest`) and the over/under-performance delta (`_diff`) accumulated into `interestForOverUnderPerformance`.
- `requestWithdraw` (`IdleCDOEpochVariant.sol:773-790`), which fixes the receipt amount as `principal + interest - totalFees` and calls `IdleCreditVault.requestWithdraw`.
- `writeOffDeposit` (`IdleCDOEpochVariant.sol:944-962`), which scales the truncated interest by `(_epochDuration + bufferPeriod) / _epochDuration` and subtracts it from `expectedEpochInterest`.
- The discounted mid-epoch deposit path (`IdleCDOEpochVariant.sol:704-728`), where `interest` / `trancheInterest` determine both `expectedEpochInterest` and the minted share count `(_amount + trancheInterest) * _trancheTotSupply / expectedFinal`.

For any `_apr` in `(0, 100)`, `_apr / 100 == 0`, so `_calcInterest` returns 0 for every amount: withdrawal requests receive principal only, `interestForOverUnderPerformance` is wrong, and `expectedEpochInterest` contributions become zero. For `_apr` between 100 and 199, up to ~50% of the true APR is silently dropped; the worst-case relative loss approaches 99 units of APR. The overflow-safety ordering cannot justify the truncation here because `_amount`, `epochDuration`, and the denominator are all multiplied anyway — multiplying `_amount * _apr` first and dividing by `(100 * 365 days * ONE_TRANCHE_TOKEN)` preserves identical overflow bounds while eliminating the loss.

Note this differs from the codebase's deliberate, documented truncation choices (e.g., "precision loss favoring the AA tranche" in `_virtualPriceAux` at `IdleCDOCreditVault.sol:326` and `IdleCDO.sol:400`), where the dust is bounded and intentionally directed. Here the lost fraction is proportional to `(_apr % 100) / _apr`, which can be arbitrarily close to 100% of the interest.

### Impact Explanation
The truncated interest flows into two concrete fund movements:

1. **Withdrawal underpayment**: a KYC-passed lender calling `requestWithdraw` during the buffer/running phase locks a receipt of `principal + interest - totalFees`. When `_apr` truncates (especially `_apr < 100` → `interest == 0`), the receipt shortchanges the withdrawer. The unpaid interest remains in live NAV and accrues to the remaining tranche holders — a direct transfer of yield from withdrawers to stayers, not bounded dust.
2. **Discounted-deposit dilution**: in the mid-epoch deposit path, `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal`. With `trancheInterest` computed on the truncated APR, both the numerator credit and `expectedEpochInterest` are understated, so a new depositor mints at a price that does not reflect true accrued yield, diluting existing holders at epoch end.

The unprivileged attacker path: a tranche-token holder requests a withdrawal (or a new lender deposits mid-epoch) in an epoch where the borrower-set APR truncates; the mispriced receipt/mint is settled by `claimWithdrawRequest`/epoch accounting against real pool funds.

### Likelihood Explanation
Likelihood depends on the APR unit scale used by `IdleCreditVault`/`unscaledApr`, which I could not fully confirm from the indexed snippets (the grep for `unscaledApr`/`setApr` in `contracts/strategies/idle/IdleCreditVault.sol` returned only match counts, not lines). If APR is expressed with sub-100 granularity legitimately reachable (e.g., bps-like units where values < 100 are valid low rates), the interest is fully zeroed — common in low-rate credit pools. If APR is always a large multiple of 100, impact degrades to bounded dust and the issue is informational. The invariant broken is fair mint/burn / one-receipt-one-payout; no existing guard (skim, flags, KYC, only-CDO) constrains the arithmetic itself.

### Recommendation
Reorder to multiply before dividing in `contracts/IdleCDOEpochVariant.sol:807-809`:

```solidity
function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * _apr * epochDuration / (100 * 365 days * ONE_TRANCHE_TOKEN);
}
```

Audit all callers (`_calcInterestWithdrawRequest`, `writeOffDeposit`, the discounted deposit path, `maxWithdrawable`) for consistency, and apply the same fix to the test-helper mirror at `test/foundry/IdleCreditVault.t.sol:3556-3558` so expected values match.

### Proof of Concept
A Foundry fork PoC should: (1) deploy the credit vault with an epoch duration and set the borrower APR to a value with a non-zero remainder mod 100 (e.g., 99 or 150 in the vault's APR units) via `stopEpochWithDuration`; (2) have a KYC'd lender deposit tranche tokens, then call `requestWithdraw` during the buffer phase; (3) assert the receipt amount equals `principal + interest(computed with exact APR) - fees` and show the shortfall versus the actual `_underlyings` returned; (4) additionally, deposit mid-epoch after the request and assert the minted share count differs from an exact-math reference, quantifying the dilution. I could not complete line-level verification of the APR scale or run the PoC within the available search iterations, so the APR-unit assumption should be confirmed first in `IdleCreditVault.setApr`/`unscaledApr`.