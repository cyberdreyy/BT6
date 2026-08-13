# Q184: derive_staked_onramp_from_vote: derivation helper and runtime validator disagree [mixed-group-and-bank-objects] [runtime-recheck]

## Question
Can an unprivileged attacker exploit mixed group and bank objects that share the same mint family so `derive_staked_onramp_from_vote` and its runtime validator disagree on the canonical derived address, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and leading to `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: mixed group and bank objects that share the same mint family
- Exploit idea: Compare helper derivations in type/utils code with the constraints enforced by instruction entrypoints. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Generate addresses from both derivation viewpoints and assert runtime accepts only the exact canonical output. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
