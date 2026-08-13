# Q2509: juplend_init_position: init path accepts stale external market identity [a-precomputed-attacker-favored-derived] [one-time]

## Question
Can an unprivileged attacker use `juplend_init_position` with a precomputed attacker-favored derived position address so `juplend_init_position` binds to a stale or wrong external market identity, violating `Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once` and causing `High: later value redirection or durable user lock`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/init_position.rs` / `juplend_init_position`
- Entrypoint: `juplend_init_position`
- Attacker controls: a precomputed attacker-favored derived position address
- Exploit idea: Probe whether init validates the exact external market/reserve expected by the bank config, not just a shape-compatible object. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once
- Expected Immunefi impact: High: later value redirection or durable user lock
- Fast validation: Feed alternate market identities and assert initialization rejects unless they match the configured bank context exactly. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
