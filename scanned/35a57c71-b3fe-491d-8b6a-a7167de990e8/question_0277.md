# Q277: derive_juplend_lending: seed material omits a security-critical dimension [a-position-init-that-mixes] [seed-domain]

## Question
Can an unprivileged attacker use `juplend_init_position` with a position init that mixes one market PDA with another reserve context so `derive_juplend_lending` trusts a PDA/address whose seed material omits a security-critical dimension, breaking `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: a position init that mixes one market PDA with another reserve context
- Exploit idea: Look for missing user, bank, group, mint, or market components in derived-address binding assumptions. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Vary one candidate seed dimension at a time and assert every accepted address changes exactly when security-critical context changes. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
