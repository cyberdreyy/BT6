# Q62: derive_single_pool_keys_from_vote: derivation helper and runtime validator disagree [mixed-staked-onramp-and-single] [runtime-recheck]

## Question
Can an unprivileged attacker exploit mixed staked onramp and single-pool contexts in the same transaction so `derive_single_pool_keys_from_vote` and its runtime validator disagree on the canonical derived address, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and leading to `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: mixed staked onramp and single-pool contexts in the same transaction
- Exploit idea: Compare helper derivations in type/utils code with the constraints enforced by instruction entrypoints. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Generate addresses from both derivation viewpoints and assert runtime accepts only the exact canonical output. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
