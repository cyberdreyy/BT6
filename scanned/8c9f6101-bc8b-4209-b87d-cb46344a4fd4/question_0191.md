# Q191: derive_staked_onramp_from_vote: derivation helper and runtime validator disagree [candidate-accounts-from-a-sibling] [seed-domain]

## Question
Can an unprivileged attacker exploit candidate accounts from a sibling staked pool so `derive_staked_onramp_from_vote` and its runtime validator disagree on the canonical derived address, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and leading to `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: candidate accounts from a sibling staked pool
- Exploit idea: Compare helper derivations in type/utils code with the constraints enforced by instruction entrypoints. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Generate addresses from both derivation viewpoints and assert runtime accepts only the exact canonical output. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
