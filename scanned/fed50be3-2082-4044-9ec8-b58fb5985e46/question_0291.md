# Q291: derive_juplend_lending: stored PDA is canonical at init but not revalidated later [attacker-prederived-market-pdas-with] [seed-domain]

## Question
Can an unprivileged attacker make `juplend_init_position` reach `derive_juplend_lending` with attacker-prederived market PDAs with valid-looking owners so a stored PDA/address canonical at init is later used without revalidation, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: attacker-prederived market PDAs with valid-looking owners
- Exploit idea: Audit flows that persist derived addresses and later trust them blindly when accounts can be caller-supplied again. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Corrupt or substitute the later-supplied account and assert the runtime path recomputes and rechecks the canonical address. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
