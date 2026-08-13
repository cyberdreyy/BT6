# Q1666: kamino_init_obligation: initialization binds a foreign external identity to marginfi state [a-user-metadata-account-from] [future-trust]

## Question
Can an unprivileged attacker call `kamino_init_obligation` with a user metadata account from another user under the same market so `kamino_init_obligation` initializes marginfi integration state against a foreign external identity, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: a user metadata account from another user under the same market
- Exploit idea: Probe initialization of obligations, positions, user metadata, and authority records for cross-user or cross-market binding mistakes. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize using mixed user and market objects, then assert the resulting state is rejected or bound only to the canonical caller context. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
