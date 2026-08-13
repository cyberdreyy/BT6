# Q349: derive_juplend_lending: attacker-chosen prederived address passes because only bump/owner is checked [candidate-pdas-from-another-bank] [seed-domain]

## Question
Can an unprivileged attacker route `juplend_init_position` through `derive_juplend_lending` with candidate PDAs from another bank that shares the same mint so an attacker-chosen prederived address passes because only owner/bump/type is checked, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: candidate PDAs from another bank that shares the same mint
- Exploit idea: Verify that runtime constraints recompute the full PDA, not just structural properties. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Supply prederived same-owner candidates and assert runtime rejects every candidate except the exact recomputed PDA. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
