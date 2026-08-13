# Q1161: kamino_deposit: deposit binds the wrong reserve, obligation, or vault [a-deposit-immediately-followed-by] [owner-binding]

## Question
Can an unprivileged attacker call `kamino_deposit` with a deposit immediately followed by borrow or withdraw investigation path so `kamino_deposit` deposits into the wrong reserve/obligation/vault context, violating `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: a deposit immediately followed by borrow or withdraw investigation path
- Exploit idea: Probe CPI account binding so superficially compatible external accounts cannot redirect where user assets or derivative shares go. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Provide mixed valid-looking reserve/obligation/vault accounts and assert deposit rejects unless the canonical configured set is used. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
