# Q250: derive_staked_onramp_from_vote: staked-onramp derivation can be bound to the wrong validator identity [a-precomputed-attacker-pda-candidate] [runtime-recheck]

## Question
Can an unprivileged attacker exploit a precomputed attacker PDA candidate with the right owner so `derive_staked_onramp_from_vote` derives or stores a staked-onramp address under the wrong validator identity, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and leading to `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a precomputed attacker PDA candidate with the right owner
- Exploit idea: This matters because later pricing and routing rely on that validator relationship being canonical. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Try alternate vote-account identities and assert only the canonical validator-derived address is accepted or stored. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
