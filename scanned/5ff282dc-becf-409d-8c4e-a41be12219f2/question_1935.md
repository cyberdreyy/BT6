# Q1935: juplend_deposit: deposit binds the wrong reserve, obligation, or vault [repeated-boundary-sized-deposit-withdraw] [owner-binding]

## Question
Can an unprivileged attacker call `juplend_deposit` with repeated boundary-sized deposit/withdraw cycles so `juplend_deposit` deposits into the wrong reserve/obligation/vault context, violating `Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only` and causing `Critical: phantom collateral credit or redirected external position`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: repeated boundary-sized deposit/withdraw cycles
- Exploit idea: Probe CPI account binding so superficially compatible external accounts cannot redirect where user assets or derivative shares go. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Juplend deposit must map exact net deposited value to exact internal credit on the configured market and supply position only
- Expected Immunefi impact: Critical: phantom collateral credit or redirected external position
- Fast validation: Provide mixed valid-looking reserve/obligation/vault accounts and assert deposit rejects unless the canonical configured set is used. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
