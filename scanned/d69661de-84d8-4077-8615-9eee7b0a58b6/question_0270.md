# Q270: derive_juplend_lending: derived address can be confused across economic contexts [candidate-pdas-from-another-bank] [runtime-recheck]

## Question
Can an unprivileged attacker exploit candidate PDAs from another bank that shares the same mint so `derive_juplend_lending` derives or accepts the same-looking PDA/address across different economic contexts, violating `Juplend market PDAs must bind to the exact configured market and mint context for every live integration path` and causing `High: wrong-market integration binding causing theft or phantom value`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_lending`
- Entrypoint: `juplend_init_position`
- Attacker controls: candidate PDAs from another bank that shares the same mint
- Exploit idea: Audit seed domain separation for every derived authority, vault, reserve, or staked helper address used by public flows. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend market PDAs must bind to the exact configured market and mint context for every live integration path
- Expected Immunefi impact: High: wrong-market integration binding causing theft or phantom value
- Fast validation: Generate derivations across neighboring contexts and assert no public path accepts a PDA derived for another context. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
