# Q1693: lib::is_precompile — nonce replay

## Question
Can an unprivileged attacker, through a transaction executed through the SVM/Bank by an unprivileged fee-payer, reach `lib::is_precompile` and reuse a durable-nonce transaction so it executes twice, or so a failed nonce tx escapes its fee, so that the invariant "each nonce value authorizes exactly one committed transaction" is violated, leading to Loss of Funds?

## Target
- File/function: `svm-callback/src/lib.rs` -> `is_precompile`
- Entrypoint: a transaction executed through the SVM/Bank by an unprivileged fee-payer
- Attacker controls: a durable-nonce transaction it can resubmit
- Exploit idea: Reuse a durable-nonce transaction so it executes twice, or so a failed nonce tx escapes its fee.
- Invariant to test: each nonce value authorizes exactly one committed transaction.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write an SVM/bank test running the transaction twice and asserting deterministic, exact fee/rollback/rent accounting.
