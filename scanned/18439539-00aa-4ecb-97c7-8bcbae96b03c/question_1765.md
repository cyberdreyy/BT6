# Q1765: kamino_init_obligation: init writes a PDA or authority derived from caller-controlled but insufficiently bound seeds [same-slot-init-plus-first] [one-time]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with same-slot init plus first deposit with altered auxiliary accounts so `kamino_init_obligation` writes or trusts a PDA/authority derived from insufficiently bound seeds, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and leading to `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on one-time initialization guarantees and partial-init replay safety.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: same-slot init plus first deposit with altered auxiliary accounts
- Exploit idea: Audit init code that accepts prederived addresses or stores derivations for later trusted use. Focus specifically on one-time initialization guarantees and partial-init replay safety.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Attempt seed-equivalent or cross-context derivations and assert init rejects every address not canonical for the target market/user pair. Attempt init, partial failure, and replay sequences and assert the state cannot be rebound or overwritten.
