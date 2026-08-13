# Q21: derive_single_pool_keys_from_vote: seed material omits a security-critical dimension [a-replay-of-a-previously] [seed-domain]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with a replay of a previously valid derivation context against a different bank seed so `derive_single_pool_keys_from_vote` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a replay of a previously valid derivation context against a different bank seed
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
