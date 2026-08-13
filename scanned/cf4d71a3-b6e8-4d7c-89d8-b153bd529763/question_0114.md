# Q114: derive_single_pool_keys_from_vote: staked-onramp derivation can be bound to the wrong validator identity [two-validator-vote-accounts-with] [runtime-recheck]

## Question
Can an unprivileged attacker exploit two validator vote accounts with similar surrounding context so `derive_single_pool_keys_from_vote` derives or stores a staked-onramp address under the wrong validator identity, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and leading to `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: two validator vote accounts with similar surrounding context
- Exploit idea: This matters because later pricing and routing rely on that validator relationship being canonical. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Try alternate vote-account identities and assert only the canonical validator-derived address is accepted or stored. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
