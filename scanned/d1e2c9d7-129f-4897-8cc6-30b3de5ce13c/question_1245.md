# Q1245: kamino_deposit: deposit path double counts external and internal balances [optional-accounts-that-influence-reward] [owner-binding]

## Question
Can an unprivileged attacker invoke `kamino_deposit` with optional accounts that influence reward or owner resolution so `kamino_deposit` counts both the pre-deposit and post-deposit position as owned value, violating `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: optional accounts that influence reward or owner resolution
- Exploit idea: Audit transitions where existing external positions are initialized, topped up, or re-read during the same call. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Seed partial positions, deposit again, and assert the internal asset view increases only by net new externally owned value. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
