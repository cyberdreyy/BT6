# Q1762: kamino_init_obligation: init writes a PDA or authority derived from caller-controlled but insufficiently bound seeds [a-user-metadata-account-from] [future-trust]

## Question
Can an unprivileged attacker use `kamino_init_obligation` with a user metadata account from another user under the same market so `kamino_init_obligation` writes or trusts a PDA/authority derived from insufficiently bound seeds, violating `Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once` and leading to `High: future value redirection, permanent user lock, or cross-user position takeover`? Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/init_obligation.rs` / `kamino_init_obligation`
- Entrypoint: `kamino_init_obligation`
- Attacker controls: a user metadata account from another user under the same market
- Exploit idea: Audit init code that accepts prederived addresses or stores derivations for later trusted use. Focus specifically on fields written at init that later deposit/withdraw/harvest paths trust without recomputation.
- Invariant to test: Kamino obligation initialization must bind user, market, reserve, and owner metadata canonically and only once
- Expected Immunefi impact: High: future value redirection, permanent user lock, or cross-user position takeover
- Fast validation: Attempt seed-equivalent or cross-context derivations and assert init rejects every address not canonical for the target market/user pair. Initialize the controlled edge state, then run the dependent later path and assert no future trust violation appears.
