# Q300: derive_juplend_lending: stored PDA is canonical at init but not revalidated later [mixed-mint-and-market-families] [runtime-recheck]

## Question
Can an unprivileged attacker make `juplend_init_position` reach `derive_juplend_lending` with mixed mint and market families in the same add/init workflow so a stored PDA/address canonical at init is later used without revalidation, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: mixed mint and market families in the same add/init workflow
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
