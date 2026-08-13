# Q308: derive_juplend_lending: derivation helper and runtime validator disagree [attacker-prederived-market-pdas-with] [runtime-recheck]

## Question
Can an unprivileged attacker exploit attacker-prederived market PDAs with valid-looking owners so `derive_juplend_lending` and its runtime validator disagree on the canonical derived address, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and leading to `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: attacker-prederived market PDAs with valid-looking owners
- Exploit idea: Compare helper derivations in type/utils code with the constraints enforced by instruction entrypoints. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Generate addresses from both derivation viewpoints and assert runtime accepts only the exact canonical output. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
