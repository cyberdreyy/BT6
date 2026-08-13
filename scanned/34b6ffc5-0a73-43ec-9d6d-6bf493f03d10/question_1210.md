# Q1210: kamino_deposit: deposit intermediary transfer and CPI accounting diverge [a-deposit-immediately-followed-by] [net-value]

## Question
Can an unprivileged attacker reach `kamino_deposit` through `kamino_deposit` with a deposit immediately followed by borrow or withdraw investigation path so intermediary token transfer and CPI deposit accounting diverge, breaking `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: a deposit immediately followed by borrow or withdraw investigation path
- Exploit idea: Look for separate phases where user funds move to an intermediary owner/vault before the external CPI settles. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Force controlled transfer/account edges and assert internal credits only appear if the external CPI consumed the matching amount. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
