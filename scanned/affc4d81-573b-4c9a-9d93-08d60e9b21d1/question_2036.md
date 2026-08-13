# Q2036: juplend_deposit: deposit can initialize a toxic integration state for later theft [a-deposit-amount-at-tiny] [net-value]

## Question
Can an unprivileged attacker use `juplend_deposit` with a deposit amount at tiny and one-share boundary conditions so `juplend_deposit` creates a valid-looking but toxic integration state that later enables `Critical: phantom collateral credit or redirected external position` by violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: a deposit amount at tiny and one-share boundary conditions
- Exploit idea: Look for one-time initialization or first-deposit side effects that later withdrawals/harvests trust blindly. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: After creating the controlled initial state, execute the dependent follow-on action and assert no later path can extract or misroute value. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
