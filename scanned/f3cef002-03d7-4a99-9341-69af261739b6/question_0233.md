# Q233: derive_staked_onramp_from_vote: vault derivation helper can point value-moving code at the wrong vault family [a-precomputed-attacker-pda-candidate] [seed-domain]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with a precomputed attacker PDA candidate with the right owner so `derive_staked_onramp_from_vote` points value-moving code at the wrong vault family via derivation confusion, breaking `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a precomputed attacker PDA candidate with the right owner
- Exploit idea: Audit helpers used to derive liquidity, insurance, fee, and intermediary vaults and their later binding in transfer paths. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Cross-substitute vault-family PDAs and assert no transfer path accepts a vault from another family. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
