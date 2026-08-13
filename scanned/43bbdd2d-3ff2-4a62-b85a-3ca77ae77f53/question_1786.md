# Q1786: kamino_init_obligation: init path double-counts the first funded position [optional-accounts-influencing-farm-or] [future-trust]

## Question
Can an unprivileged attacker make `kamino_init_obligation` reach `kamino_init_obligation` with optional accounts influencing farm or owner setup so the first funded integration position is counted twice internally, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: optional accounts influencing farm or owner setup
- Exploit idea: Look for init-plus-deposit sequences where both phases may assume they are creating net new value. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize and first-fund under adversarial amounts and assert internal asset view increases exactly once by the external net value. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
