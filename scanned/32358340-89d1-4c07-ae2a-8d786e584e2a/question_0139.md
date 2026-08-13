# Q139: derive_staked_onramp_from_vote: derived address can be confused across economic contexts [stale-auxiliary-state-from-a] [seed-domain]

## Question
Can an unprivileged attacker exploit stale auxiliary state from a previous onramp mode so `derive_staked_onramp_from_vote` derives or accepts the same-looking PDA/address across different economic contexts, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: stale auxiliary state from a previous onramp mode
- Exploit idea: Audit seed domain separation for every derived authority, vault, reserve, or staked helper address used by public flows. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Generate derivations across neighboring contexts and assert no public path accepts a PDA derived for another context. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
