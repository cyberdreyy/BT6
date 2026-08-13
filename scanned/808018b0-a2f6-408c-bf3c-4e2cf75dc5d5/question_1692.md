# Q1692: kamino_init_obligation: init path can be replayed to overwrite a live position context [cross-group-bank-and-external] [future-trust]

## Question
Can an unprivileged attacker replay `kamino_init_obligation` with cross-group bank and external market pairings so `kamino_init_obligation` overwrites or rebinds a live integration position context, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: cross-group bank and external market pairings
- Exploit idea: Check whether init paths are truly one-time and reject partially initialized but live contexts. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Run init twice with changed auxiliary accounts and assert the second call is a strict no-op or hard failure. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
