# Q1233: kamino_deposit: deposit path double counts external and internal balances [a-reserve-and-obligation-from] [owner-binding]

## Question
Can an unprivileged attacker invoke `kamino_deposit` with a reserve and obligation from two different but type-compatible Kamino contexts so `kamino_deposit` counts both the pre-deposit and post-deposit position as owned value, violating `Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context` and causing `Critical: phantom collateral credit or direct fund redirection`? Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/deposit.rs` / `kamino_deposit`
- Entrypoint: `kamino_deposit`
- Attacker controls: a reserve and obligation from two different but type-compatible Kamino contexts
- Exploit idea: Audit transitions where existing external positions are initialized, topped up, or re-read during the same call. Focus specifically on whether the resulting external position and any future rewards bind to the canonical owner only.
- Invariant to test: Kamino deposit must credit only the value truly deposited into the exact configured reserve/obligation context
- Expected Immunefi impact: Critical: phantom collateral credit or direct fund redirection
- Fast validation: Seed partial positions, deposit again, and assert the internal asset view increases only by net new externally owned value. Deposit under mixed owner and position contexts and assert every resulting derivative or reward path still belongs to the canonical owner.
