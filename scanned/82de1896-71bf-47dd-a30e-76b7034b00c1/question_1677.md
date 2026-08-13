# Q1677: kamino_init_obligation: initialization binds a foreign external identity to marginfi state [a-precomputed-attacker-favored-obligation] [one-time]

## Question
Can an unprivileged attacker call `kamino_init_obligation` with a precomputed attacker-favored obligation-related PDA so `kamino_init_obligation` initializes marginfi integration state against a foreign external identity, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: a precomputed attacker-favored obligation-related PDA
- Exploit idea: Probe initialization of obligations, positions, user metadata, and authority records for cross-user or cross-market binding mistakes. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize using mixed user and market objects, then assert the resulting state is rejected or bound only to the canonical caller context. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
