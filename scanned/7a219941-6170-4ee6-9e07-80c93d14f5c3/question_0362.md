# Q362: derive_juplend_lending: vault derivation helper can point value-moving code at the wrong vault family [omitted-or-reordered-auxiliary-accounts] [runtime-recheck]

## Question
Can an unprivileged attacker use `juplend_init_position` with omitted or reordered auxiliary accounts around Juplend init so `derive_juplend_lending` points value-moving code at the wrong vault family via derivation confusion, breaking `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: omitted or reordered auxiliary accounts around Juplend init
- Exploit idea: Audit helpers used to derive liquidity, insurance, fee, and intermediary vaults and their later binding in transfer paths. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Cross-substitute vault-family PDAs and assert no transfer path accepts a vault from another family. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
