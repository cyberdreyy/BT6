# Q1758: kamino_init_obligation: init path can be used to grief-freeze third-party funds durably [a-precomputed-attacker-favored-obligation] [future-trust]

## Question
Can an unprivileged attacker call `kamino_init_obligation` with a precomputed attacker-favored obligation-related PDA so `kamino_init_obligation` creates a durable integration state that freezes or strands third-party funds, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: a precomputed attacker-favored obligation-related PDA
- Exploit idea: Even without immediate theft, initialization bugs are in scope if they create a permanent lock on real value. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize the controlled edge case, then test every intended recovery/close path and assert users can still reclaim value. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
