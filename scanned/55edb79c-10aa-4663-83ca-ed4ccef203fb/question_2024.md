# Q2024: juplend_deposit: deposit uses the right accounts but wrong amount domain [remaining-accounts-that-can-swap] [net-value]

## Question
Can an unprivileged attacker call `juplend_deposit` with remaining accounts that can swap market and reserve-like contexts so `juplend_deposit` measures external deposit value in the wrong amount domain, breaking `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: remaining accounts that can swap market and reserve-like contexts
- Exploit idea: Stress token amount vs share amount vs fee-adjusted amount conversions around the external CPI boundary. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Fuzz boundary amounts and assert internal and external ledgers reconcile exactly after every deposit. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
