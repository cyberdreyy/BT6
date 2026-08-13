# Q30: derive_single_pool_keys_from_vote: seed material omits a security-critical dimension [mixed-staked-onramp-and-single] [runtime-recheck]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with mixed staked onramp and single-pool contexts in the same transaction so `derive_single_pool_keys_from_vote` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: mixed staked onramp and single-pool contexts in the same transaction
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
