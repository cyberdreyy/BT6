# Q223: derive_staked_onramp_from_vote: attacker-chosen prederived address passes because only bump/owner is checked [candidate-accounts-from-a-sibling] [seed-domain]

## Question
Can an unprivileged attacker route `enable_staked_oracle_onramp` through `derive_staked_onramp_from_vote` with candidate accounts from a sibling staked pool so an attacker-chosen prederived address passes because only owner/bump/type is checked, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: candidate accounts from a sibling staked pool
- Exploit idea: Verify that runtime constraints recompute the full PDA, not just structural properties. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Supply prederived same-owner candidates and assert runtime rejects every candidate except the exact recomputed PDA. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
