# Q365: derive_juplend_lending: vault derivation helper can point value-moving code at the wrong vault family [candidate-pdas-from-another-bank] [seed-domain]

## Question
Can an unprivileged attacker use `juplend_init_position` with candidate PDAs from another bank that shares the same mint so `derive_juplend_lending` points value-moving code at the wrong vault family via derivation confusion, breaking `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: candidate PDAs from another bank that shares the same mint
- Exploit idea: Audit helpers used to derive liquidity, insurance, fee, and intermediary vaults and their later binding in transfer paths. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Cross-substitute vault-family PDAs and assert no transfer path accepts a vault from another family. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
