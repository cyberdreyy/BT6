# Q36: derive_single_pool_keys_from_vote: stored PDA is canonical at init but not revalidated later [a-permissionless-add-pool-flow] [runtime-recheck]

## Question
Can an unprivileged attacker make `lending_pool_add_bank_permissionless` reach `derive_single_pool_keys_from_vote` with a permissionless add-pool flow that receives alternate vote-derived accounts so a stored PDA/address canonical at init is later used without revalidation, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a permissionless add-pool flow that receives alternate vote-derived accounts
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
