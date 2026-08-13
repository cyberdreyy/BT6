# Q93: derive_single_pool_keys_from_vote: attacker-chosen prederived address passes because only bump/owner is checked [mixed-staked-onramp-and-single] [seed-domain]

## Question
Can an unprivileged attacker route `lending_pool_add_bank_permissionless` through `derive_single_pool_keys_from_vote` with mixed staked onramp and single-pool contexts in the same transaction so an attacker-chosen prederived address passes because only owner/bump/type is checked, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: mixed staked onramp and single-pool contexts in the same transaction
- Exploit idea: Verify that runtime constraints recompute the full PDA, not just structural properties. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Supply prederived same-owner candidates and assert runtime rejects every candidate except the exact recomputed PDA. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
