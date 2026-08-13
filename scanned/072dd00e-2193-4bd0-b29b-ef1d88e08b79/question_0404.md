# Q404: derive_juplend_supply_position: seed material omits a security-critical dimension [precomputed-attacker-controlled-pda-candidates] [runtime-recheck]

## Question
Can an unprivileged attacker use `juplend_deposit` with precomputed attacker-controlled PDA candidates so `derive_juplend_supply_position` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `Juplend supply-position derivation must be unique to the intended owner, bank, and external market context` and causing `High: value redirected to the wrong external position or later reward theft`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_supply_position`
- Entrypoint: `juplend_deposit`
- Attacker controls: precomputed attacker-controlled PDA candidates
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend supply-position derivation must be unique to the intended owner, bank, and external market context
- Expected Immunefi impact: High: value redirected to the wrong external position or later reward theft
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
