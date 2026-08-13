# Q1994: juplend_deposit: optional or remaining accounts redirect derivative ownership [a-deposit-followed-immediately-by] [net-value]

## Question
Can an unprivileged attacker use `juplend_deposit` with a deposit followed immediately by borrow or withdraw investigation path so `juplend_deposit` routes resulting derivative ownership or reward accrual to the wrong owner, violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and leading to `Critical: phantom collateral credit or redirected external position`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: a deposit followed immediately by borrow or withdraw investigation path
- Exploit idea: Probe optional accounts and owner metadata used during integration deposits to ensure derivative positions always belong to the intended protocol/user owner. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Provide attacker-controlled optional accounts and assert resulting positions and rewards still bind to the canonical owner only. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
