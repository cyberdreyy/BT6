# Q212: derive_staked_onramp_from_vote: attacker-chosen prederived address passes because only bump/owner is checked [a-mode-switch-path-that] [runtime-recheck]

## Question
Can an unprivileged attacker route `enable_staked_oracle_onramp` through `derive_staked_onramp_from_vote` with a mode-switch path that stores or trusts attacker-supplied derived accounts so an attacker-chosen prederived address passes because only owner/bump/type is checked, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a mode-switch path that stores or trusts attacker-supplied derived accounts
- Exploit idea: Verify that runtime constraints recompute the full PDA, not just structural properties. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Supply prederived same-owner candidates and assert runtime rejects every candidate except the exact recomputed PDA. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
