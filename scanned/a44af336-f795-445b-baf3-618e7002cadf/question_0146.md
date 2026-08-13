# Q146: derive_staked_onramp_from_vote: seed material omits a security-critical dimension [alternate-vote-accounts-with-matching] [runtime-recheck]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with alternate vote accounts with matching interface but wrong validator lineage so `derive_staked_onramp_from_vote` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks` and causing `High: exploitable misbinding of staked pricing or durable user freeze`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_staked_onramp_from_vote`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: alternate vote accounts with matching interface but wrong validator lineage
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: onramp derivation from validator identity must remain canonical and non-substitutable across groups and banks
- Expected Immunefi impact: High: exploitable misbinding of staked pricing or durable user freeze
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
