# Q1705: kamino_init_obligation: init path seeds later value redirection through wrong ownership metadata [optional-accounts-influencing-farm-or] [one-time]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with optional accounts influencing farm or owner setup so `kamino_init_obligation` stores wrong ownership metadata that later redirects value, breaking `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and leading to `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: optional accounts influencing farm or owner setup
- Exploit idea: Audit any stored owner, authority, stats, or pool-id fields consumed later by deposit/withdraw/harvest flows. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Create the controlled initial state, then run the dependent follow-on flow and assert outputs still belong to the expected owner only. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
