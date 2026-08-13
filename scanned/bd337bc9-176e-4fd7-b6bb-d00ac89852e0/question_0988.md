# Q988: calculate_post_fee_spl_deposit_amount: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [replay-of-a-valid-settlement] [family-binding]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with replay of a valid settlement context after one success so `calculate_post_fee_spl_deposit_amount` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: replay of a valid settlement context after one success
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
