# Q1262: kamino_deposit: deposit uses the right accounts but wrong amount domain [optional-accounts-that-influence-reward] [net-value]

## Question
Can an unprivileged attacker call `kamino_deposit` with optional accounts that influence reward or owner resolution so `kamino_deposit` measures external deposit value in the wrong amount domain, breaking `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: optional accounts that influence reward or owner resolution
- Exploit idea: Stress token amount vs share amount vs fee-adjusted amount conversions around the external CPI boundary. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Fuzz boundary amounts and assert internal and external ledgers reconcile exactly after every deposit. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
