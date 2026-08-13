# Q1734: kamino_init_obligation: init path accepts stale external market identity [same-slot-init-plus-first] [future-trust]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with same-slot init plus first deposit with altered auxiliary accounts so `kamino_init_obligation` binds to a stale or wrong external market identity, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: same-slot init plus first deposit with altered auxiliary accounts
- Exploit idea: Probe whether init validates the exact external market/reserve expected by the bank config, not just a shape-compatible object. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Feed alternate market identities and assert initialization rejects unless they match the configured bank context exactly. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
