# Q2014: bank::last_blockhash_and_lamports_per_signature — transaction-batch locking

## Question
Can an unprivileged attacker, through a transaction committed to the Bank by an unprivileged fee-payer, reach `bank::last_blockhash_and_lamports_per_signature` and craft account locks so a batch acquires conflicting write locks or deadlocks the bank, so that the invariant "batched transactions never hold conflicting locks on the same account" is violated, leading to DoS (replay stall)?

## Target
- File/function: `runtime/src/bank.rs` -> `last_blockhash_and_lamports_per_signature`
- Entrypoint: a transaction committed to the Bank by an unprivileged fee-payer
- Attacker controls: the write/read account sets across transactions in a batch
- Exploit idea: Craft account locks so a batch acquires conflicting write locks or deadlocks the bank.
- Invariant to test: batched transactions never hold conflicting locks on the same account.
- Expected Immunefi impact: DoS (replay stall) — High
- Fast validation: write a bank test committing the block on two independent banks and asserting identical bank hash / dedup behavior.
