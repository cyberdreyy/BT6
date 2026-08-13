# Q1162: kamino_deposit: deposit binds the wrong reserve, obligation, or vault [a-deposit-immediately-followed-by] [net-value]

## Question
Can an unprivileged attacker call `kamino_deposit` with a deposit immediately followed by borrow or withdraw investigation path so `kamino_deposit` deposits into the wrong reserve/obligation/vault context, violating `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: a deposit immediately followed by borrow or withdraw investigation path
- Exploit idea: Probe CPI account binding so superficially compatible external accounts cannot redirect where user assets or derivative shares go. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Provide mixed valid-looking reserve/obligation/vault accounts and assert deposit rejects unless the canonical configured set is used. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
