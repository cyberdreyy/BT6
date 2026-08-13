# Q1759: kamino_init_obligation: init path can be used to grief-freeze third-party funds durably [partial-setup-failure-between-user] [one-time]

## Question
Can an unprivileged attacker call `kamino_init_obligation` with partial setup failure between user metadata, obligation, and farms so `kamino_init_obligation` creates a durable integration state that freezes or strands third-party funds, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: partial setup failure between user metadata, obligation, and farms
- Exploit idea: Even without immediate theft, initialization bugs are in scope if they create a permanent lock on real value. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize the controlled edge case, then test every intended recovery/close path and assert users can still reclaim value. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
