# Q1743: kamino_init_obligation: init path accepts stale external market identity [partial-setup-failure-between-user] [one-time]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with partial setup failure between user metadata, obligation, and farms so `kamino_init_obligation` binds to a stale or wrong external market identity, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: partial setup failure between user metadata, obligation, and farms
- Exploit idea: Probe whether init validates the exact external market/reserve expected by the bank config, not just a shape-compatible object. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Feed alternate market identities and assert initialization rejects unless they match the configured bank context exactly. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
