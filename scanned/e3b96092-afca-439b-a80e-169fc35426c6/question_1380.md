# Q1380: cpi_kamino_deposit: deposit uses the right accounts but wrong amount domain [same-slot-reserve-refresh-followed] [net-value]

## Question
Can an unprivileged attacker call `kamino_deposit` with same-slot reserve refresh followed by CPI deposit into another reserve context so `cpi_kamino_deposit` measures external deposit value in the wrong amount domain, breaking `external CPI deposit and internal marginfi accounting must be economically atomic and identically sized` and causing `Critical: phantom value, protocol loss, or user fund redirection`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `cpi_kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: same-slot reserve refresh followed by CPI deposit into another reserve context
- Exploit idea: Stress token amount vs share amount vs fee-adjusted amount conversions around the external CPI boundary. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: external CPI deposit and internal marginfi accounting must be economically atomic and identically sized
- Expected Immunefi impact: Critical: phantom value, protocol loss, or user fund redirection
- Fast validation: Fuzz boundary amounts and assert internal and external ledgers reconcile exactly after every deposit. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
