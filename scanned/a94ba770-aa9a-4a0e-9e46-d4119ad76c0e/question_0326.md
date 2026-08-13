# Q326: derive_juplend_lending: PDA reuse allows authority confusion across integrations [a-position-init-that-mixes] [runtime-recheck]

## Question
Can an unprivileged attacker use `juplend_init_position` with a position init that mixes one market PDA with another reserve context so `derive_juplend_lending` reuses a PDA/authority across integrations or bank families in a way that violates `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causes `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: a position init that mixes one market PDA with another reserve context
- Exploit idea: Particularly inspect helpers shared by staked collateral, Juplend, Kamino, and generic vault flows. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Attempt cross-integration account substitution and assert no authority PDA is accepted outside its exact integration family. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
