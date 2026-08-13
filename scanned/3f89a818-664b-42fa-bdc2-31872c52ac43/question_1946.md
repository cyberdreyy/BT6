# Q1946: juplend_deposit: deposit accounting mints too much internal value [a-deposit-followed-immediately-by] [net-value]

## Question
Can an unprivileged attacker use `juplend_deposit` with a deposit followed immediately by borrow or withdraw investigation path so `juplend_deposit` credits more internal value than the external integration actually received, breaking `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: a deposit followed immediately by borrow or withdraw investigation path
- Exploit idea: Audit share conversions, fee-adjusted transfers, and rounding around CPI deposit accounting. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Compare external protocol balances against marginfi internal balances after adversarial deposit amounts and assert no phantom value is minted. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
