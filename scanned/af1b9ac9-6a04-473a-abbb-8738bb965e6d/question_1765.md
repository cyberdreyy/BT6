# Q1765: fee_distribution::calculate_reward_for_transaction — nonce replay

## Question
Can an unprivileged attacker, through a transaction executed through the SVM/Bank by an unprivileged fee-payer, reach `fee_distribution::calculate_reward_for_transaction` and reuse a durable-nonce transaction so it executes twice, or so a failed nonce tx escapes its fee, so that the invariant "each nonce value authorizes exactly one committed transaction" is violated, leading to Loss of Funds?

## Target
- File/function: `runtime/src/bank/fee_distribution.rs` -> `calculate_reward_for_transaction`
- Entrypoint: a transaction executed through the SVM/Bank by an unprivileged fee-payer
- Attacker controls: a durable-nonce transaction it can resubmit
- Exploit idea: Reuse a durable-nonce transaction so it executes twice, or so a failed nonce tx escapes its fee.
- Invariant to test: each nonce value authorizes exactly one committed transaction.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write an SVM/bank test running the transaction twice and asserting deterministic, exact fee/rollback/rent accounting.
