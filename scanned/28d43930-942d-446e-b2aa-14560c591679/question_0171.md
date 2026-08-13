# Q171: derive_staked_onramp_from_vote: stored PDA is canonical at init but not revalidated later [stale-auxiliary-state-from-a] [seed-domain]

## Question
Can an unprivileged attacker make `enable_staked_oracle_onramp` reach `derive_staked_onramp_from_vote` with stale auxiliary state from a previous onramp mode so a stored PDA/address canonical at init is later used without revalidation, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: stale auxiliary state from a previous onramp mode
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
