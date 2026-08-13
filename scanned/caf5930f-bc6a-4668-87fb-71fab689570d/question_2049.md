# Q2049: cpi_juplend_deposit: deposit binds the wrong reserve, obligation, or vault [a-deposit-where-transfer-succeeds] [owner-binding]

## Question
Can an unprivileged attacker call `juplend_deposit` with a deposit where transfer succeeds but external CPI context is mismatched so `cpi_juplend_deposit` deposits into the wrong reserve/obligation/vault context, violating `Juplend CPI deposit and internal accounting must remain atomic and conserve value exactly` and causing `Critical: phantom value or user fund redirection`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/deposit.rs` / `cpi_juplend_deposit`
- Entrypoint: `juplend_deposit`
- Attacker controls: a deposit where transfer succeeds but external CPI context is mismatched
- Exploit idea: Probe CPI account binding so superficially compatible external accounts cannot redirect where user assets or derivative shares go. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Juplend CPI deposit and internal accounting must remain atomic and conserve value exactly
- Expected Immunefi impact: Critical: phantom value or user fund redirection
- Fast validation: Provide mixed valid-looking reserve/obligation/vault accounts and assert deposit rejects unless the canonical configured set is used. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
