# Q1704: kamino_init_obligation: init path seeds later value redirection through wrong ownership metadata [replay-of-init-against-an] [future-trust]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with replay of init against an already live or partially live obligation so `kamino_init_obligation` stores wrong ownership metadata that later redirects value, breaking `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and leading to `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: replay of init against an already live or partially live obligation
- Exploit idea: Audit any stored owner, authority, stats, or pool-id fields consumed later by deposit/withdraw/harvest flows. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Create the controlled initial state, then run the dependent follow-on flow and assert outputs still belong to the expected owner only. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
