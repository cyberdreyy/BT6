# Q1268: kamino_deposit: deposit can initialize a toxic integration state for later theft [a-deposit-amount-at-one] [net-value]

## Question
Can an unprivileged attacker use `kamino_deposit` with a deposit amount at one-share and tiny rounding boundaries so `kamino_deposit` creates a valid-looking but toxic integration state that later enables `Critical: phantom collateral credit or direct fund redirection` by violating `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: a deposit amount at one-share and tiny rounding boundaries
- Exploit idea: Look for one-time initialization or first-deposit side effects that later withdrawals/harvests trust blindly. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: After creating the controlled initial state, execute the dependent follow-on action and assert no later path can extract or misroute value. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
