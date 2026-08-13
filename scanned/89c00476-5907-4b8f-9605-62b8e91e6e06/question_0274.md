# Q274: derive_juplend_lending: seed material omits a security-critical dimension [two-juplend-markets-with-same] [runtime-recheck]

## Question
Can an unprivileged attacker use `juplend_init_position` with two Juplend markets with same-type accounts so `derive_juplend_lending` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: two Juplend markets with same-type accounts
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
