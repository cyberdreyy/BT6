# Q39: derive_single_pool_keys_from_vote: stored PDA is canonical at init but not revalidated later [derived-accounts-supplied-precomputed-by] [seed-domain]

## Question
Can an unprivileged attacker make `lending_pool_add_bank_permissionless` reach `derive_single_pool_keys_from_vote` with derived accounts supplied precomputed by the attacker so a stored PDA/address canonical at init is later used without revalidation, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: derived accounts supplied precomputed by the attacker
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
