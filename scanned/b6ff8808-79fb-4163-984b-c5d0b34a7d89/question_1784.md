# Q1784: kamino_init_obligation: init path double-counts the first funded position [replay-of-init-against-an] [future-trust]

## Question
Can an unprivileged attacker make `kamino_init_obligation` reach `kamino_init_obligation` with replay of init against an already live or partially live obligation so the first funded integration position is counted twice internally, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: replay of init against an already live or partially live obligation
- Exploit idea: Look for init-plus-deposit sequences where both phases may assume they are creating net new value. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize and first-fund under adversarial amounts and assert internal asset view increases exactly once by the external net value. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
