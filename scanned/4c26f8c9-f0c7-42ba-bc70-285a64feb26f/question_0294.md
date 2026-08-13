# Q294: derive_juplend_lending: stored PDA is canonical at init but not revalidated later [a-position-init-that-mixes] [runtime-recheck]

## Question
Can an unprivileged attacker make `juplend_init_position` reach `derive_juplend_lending` with a position init that mixes one market PDA with another reserve context so a stored PDA/address canonical at init is later used without revalidation, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: a position init that mixes one market PDA with another reserve context
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
