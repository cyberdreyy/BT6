# Q1724: kamino_init_obligation: init combines transfer and CPI setup non-atomically [cross-group-bank-and-external] [future-trust]

## Question
Can an unprivileged attacker make `kamino_init_obligation` reach `kamino_init_obligation` with cross-group bank and external market pairings so transfer/setup phases are not economically atomic during integration initialization, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: cross-group bank and external market pairings
- Exploit idea: Look for init paths that move user funds before every external and internal setup dependency is conclusively valid. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Force late-stage setup failure and assert no transferred value or partially initialized authority state remains. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
