# Q100: derive_single_pool_keys_from_vote: vault derivation helper can point value-moving code at the wrong vault family [a-permissionless-add-pool-flow] [runtime-recheck]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with a permissionless add-pool flow that receives alternate vote-derived accounts so `derive_single_pool_keys_from_vote` points value-moving code at the wrong vault family via derivation confusion, breaking `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a permissionless add-pool flow that receives alternate vote-derived accounts
- Exploit idea: Audit helpers used to derive liquidity, insurance, fee, and intermediary vaults and their later binding in transfer paths. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Cross-substitute vault-family PDAs and assert no transfer path accepts a vault from another family. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
