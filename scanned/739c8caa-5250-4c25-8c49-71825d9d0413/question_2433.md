# Q2433: juplend_init_position: initialization binds a foreign external identity to marginfi state [a-supply-position-candidate-from] [one-time]

## Question
Can an unprivileged attacker call `juplend_init_position` with a supply position candidate from another user so `juplend_init_position` initializes marginfi integration state against a foreign external identity, violating `Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once` and causing `High: later value redirection or durable user lock`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/init_position.rs` / `juplend_init_position`
- Entrypoint: `juplend_init_position`
- Attacker controls: a supply position candidate from another user
- Exploit idea: Probe initialization of obligations, positions, user metadata, and authority records for cross-user or cross-market binding mistakes. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once
- Expected Immunefi impact: High: later value redirection or durable user lock
- Fast validation: Initialize using mixed user and market objects, then assert the resulting state is rejected or bound only to the canonical caller context. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
