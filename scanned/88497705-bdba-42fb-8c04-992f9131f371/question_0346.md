# Q346: derive_juplend_lending: attacker-chosen prederived address passes because only bump/owner is checked [omitted-or-reordered-auxiliary-accounts] [runtime-recheck]

## Question
Can an unprivileged attacker route `juplend_init_position` through `derive_juplend_lending` with omitted or reordered auxiliary accounts around Juplend init so an attacker-chosen prederived address passes because only owner/bump/type is checked, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: omitted or reordered auxiliary accounts around Juplend init
- Exploit idea: Verify that runtime constraints recompute the full PDA, not just structural properties. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Supply prederived same-owner candidates and assert runtime rejects every candidate except the exact recomputed PDA. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
