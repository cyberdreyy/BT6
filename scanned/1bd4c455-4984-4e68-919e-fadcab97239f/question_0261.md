# Q261: derive_juplend_lending: derived address can be confused across economic contexts [a-position-init-that-mixes] [seed-domain]

## Question
Can an unprivileged attacker exploit a position init that mixes one market PDA with another reserve context so `derive_juplend_lending` derives or accepts the same-looking PDA/address across different economic contexts, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: a position init that mixes one market PDA with another reserve context
- Exploit idea: Audit seed domain separation for every derived authority, vault, reserve, or staked helper address used by public flows. Focus specifically on missing seed dimensions or insufficient domain separation across nearby economic contexts.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Generate derivations across neighboring contexts and assert no public path accepts a PDA derived for another context. Derive adjacent-context addresses and assert no public path accepts a PDA from the wrong context.
