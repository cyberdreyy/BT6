# Q926: calculate_post_fee_spl_deposit_amount: mint/account probing helper can be steered to the wrong token context [a-bank-whose-insurance-and] [family-binding]

## Question
Can an unprivileged attacker invoke `lending_pool_handle_bankruptcy` with a bank whose insurance and liquidity vaults share similar interfaces so `calculate_post_fee_spl_deposit_amount` probes or caches the wrong token context, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: a bank whose insurance and liquidity vaults share similar interfaces
- Exploit idea: Helpers like token-mint selection and transfer-hook probing must bind tightly to the bank and vault actually being used. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Supply same-owner or same-interface token accounts from another mint and assert helpers never approve the wrong token context. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
