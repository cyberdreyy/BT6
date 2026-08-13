# Q2494: juplend_init_position: init combines transfer and CPI setup non-atomically [a-precomputed-attacker-favored-derived] [future-trust]

## Question
Can an unprivileged attacker make `juplend_init_position` reach `juplend_init_position` with a precomputed attacker-favored derived position address so transfer/setup phases are not economically atomic during integration initialization, violating `Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once` and causing `High: later value redirection or durable user lock`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/init_position.rs` / `juplend_init_position`
- Entrypoint: `juplend_init_position`
- Attacker controls: a precomputed attacker-favored derived position address
- Exploit idea: Look for init paths that move user funds before every external and internal setup dependency is conclusively valid. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Juplend position initialization must bind market, position owner, and derivative ownership canonically and only once
- Expected Immunefi impact: High: later value redirection or durable user lock
- Fast validation: Force late-stage setup failure and assert no transferred value or partially initialized authority state remains. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
