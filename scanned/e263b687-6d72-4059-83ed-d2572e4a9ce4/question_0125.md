# Q125: derive_single_pool_keys_from_vote: staked-onramp derivation can be bound to the wrong validator identity [mixed-staked-onramp-and-single] [seed-domain]

## Question
Can an unprivileged attacker exploit mixed staked onramp and single-pool contexts in the same transaction so `derive_single_pool_keys_from_vote` derives or stores a staked-onramp address under the wrong validator identity, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and leading to `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: mixed staked onramp and single-pool contexts in the same transaction
- Exploit idea: This matters because later pricing and routing rely on that validator relationship being canonical. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Try alternate vote-account identities and assert only the canonical validator-derived address is accepted or stored. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
