# Q153: derive_staked_onramp_from_vote: seed material omits a security-critical dimension [a-precomputed-attacker-pda-candidate] [seed-domain]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with a precomputed attacker PDA candidate with the right owner so `derive_staked_onramp_from_vote` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a precomputed attacker PDA candidate with the right owner
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
