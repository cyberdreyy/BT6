# Q2023: juplend_deposit: deposit uses the right accounts but wrong amount domain [remaining-accounts-that-can-swap] [owner-binding]

## Question
Can an unprivileged attacker call `juplend_deposit` with remaining accounts that can swap market and reserve-like contexts so `juplend_deposit` measures external deposit value in the wrong amount domain, breaking `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: remaining accounts that can swap market and reserve-like contexts
- Exploit idea: Stress token amount vs share amount vs fee-adjusted amount conversions around the external CPI boundary. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Fuzz boundary amounts and assert internal and external ledgers reconcile exactly after every deposit. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
