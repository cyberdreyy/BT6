# Q2504: juplend_init_position: init path accepts stale external market identity [replay-against-a-partially-initialized] [future-trust]

## Question
Can an unprivileged attacker use `juplend_init_position` with replay against a partially initialized or already-funded position so `juplend_init_position` binds to a stale or wrong external market identity, violating `Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once` and causing `High: later value redirection or durable user lock`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/init_position.rs` / `juplend_init_position`
- Entrypoint: `juplend_init_position`
- Attacker controls: replay against a partially initialized or already-funded position
- Exploit idea: Probe whether init validates the exact external market/reserve expected by the bank config, not just a shape-compatible object. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once
- Expected Immunefi impact: High: later value redirection or durable user lock
- Fast validation: Feed alternate market identities and assert initialization rejects unless they match the configured bank context exactly. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
