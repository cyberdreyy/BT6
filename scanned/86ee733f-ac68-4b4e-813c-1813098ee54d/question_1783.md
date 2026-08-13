# Q1783: kamino_init_obligation: init path double-counts the first funded position [replay-of-init-against-an] [one-time]

## Question
Can an unprivileged attacker make `kamino_init_obligation` reach `kamino_init_obligation` with replay of init against an already live or partially live obligation so the first funded integration position is counted twice internally, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and causing `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: replay of init against an already live or partially live obligation
- Exploit idea: Look for init-plus-deposit sequences where both phases may assume they are creating net new value. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Initialize and first-fund under adversarial amounts and assert internal asset view increases exactly once by the external net value. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
