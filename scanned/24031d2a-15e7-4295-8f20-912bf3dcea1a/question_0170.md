# Q170: derive_staked_onramp_from_vote: stored PDA is canonical at init but not revalidated later [a-precomputed-attacker-pda-candidate] [runtime-recheck]

## Question
Can an unprivileged attacker make `enable_staked_oracle_onramp` reach `derive_staked_onramp_from_vote` with a precomputed attacker PDA candidate with the right owner so a stored PDA/address canonical at init is later used without revalidation, violating `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a precomputed attacker PDA candidate with the right owner
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
