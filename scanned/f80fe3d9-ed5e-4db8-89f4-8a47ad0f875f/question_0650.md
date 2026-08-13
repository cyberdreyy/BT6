# Q650: find_bank_vault_authority_pda: fee-adjusted amount conversion can be abused across CPI boundaries [same-slot-withdraw-and-reward] [family-binding]

## Question
Can an unprivileged attacker use `juplend_withdraw` with same-slot withdraw and reward-harvest paths sharing auxiliary accounts so `find_bank_vault_authority_pda` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: same-slot withdraw and reward-harvest paths sharing auxiliary accounts
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
