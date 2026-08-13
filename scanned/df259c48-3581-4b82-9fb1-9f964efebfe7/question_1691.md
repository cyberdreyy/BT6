# Q1691: kamino_init_obligation: init path can be replayed to overwrite a live position context [cross-group-bank-and-external] [one-time]

## Question
Can an unprivileged attacker replay `kamino_init_obligation` with cross-group bank and external market pairings so `kamino_init_obligation` overwrites or rebinds a live integration position context, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: cross-group bank and external market pairings
- Exploit idea: Check whether init paths are truly one-time and reject partially initialized but live contexts. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Run init twice with changed auxiliary accounts and assert the second call is a strict no-op or hard failure. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
