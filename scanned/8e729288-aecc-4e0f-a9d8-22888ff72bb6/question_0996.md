# Q996: calculate_post_fee_spl_deposit_amount: utility-derived authority selection can be reused in the wrong instruction family [token-contexts-with-edge-case] [family-binding]

## Question
Can an unprivileged attacker route `lending_pool_handle_bankruptcy` through `calculate_post_fee_spl_deposit_amount` with token contexts with edge-case fee-adjusted behavior so a utility-derived authority selection is reused in the wrong instruction family, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: token contexts with edge-case fee-adjusted behavior
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
