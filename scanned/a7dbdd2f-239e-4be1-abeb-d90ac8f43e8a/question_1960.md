# Q1960: juplend_deposit: refresh-before-deposit path can be shaped to stale acceptance [remaining-accounts-that-can-swap] [net-value]

## Question
Can an unprivileged attacker invoke `juplend_deposit` with remaining accounts that can swap market and reserve-like contexts so `juplend_deposit` relies on a stale or mismatched refresh result before depositing, violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: remaining accounts that can swap market and reserve-like contexts
- Exploit idea: Where deposits require external refresh, verify the refreshed object is exactly the one later mutated and valued. Focus specifically on exact net-value conservation across user transfer, intermediary state, external CPI, and internal credit.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Swap refresh context and deposit context in a test and assert the path cannot accept mixed external state. Compare user debit, external protocol credit, and internal asset increase and assert all three line up exactly.
