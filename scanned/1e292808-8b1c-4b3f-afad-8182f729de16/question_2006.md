# Q2006: juplend_deposit: deposit path double counts external and internal balances [same-slot-init-position-then] [net-value]

## Question
Can an unprivileged attacker invoke `juplend_deposit` with same-slot init-position then deposit with changed auxiliary accounts so `juplend_deposit` counts both the pre-deposit and post-deposit position as owned value, violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: same-slot init-position then deposit with changed auxiliary accounts
- Exploit idea: Audit transitions where existing external positions are initialized, topped up, or re-read during the same call. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Seed partial positions, deposit again, and assert the internal asset view increases only by net new externally owned value. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
