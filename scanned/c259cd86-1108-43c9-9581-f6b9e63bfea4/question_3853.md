# Q3853: add_transactions duplicate execution or effects replay

## Question
Can an unprivileged attacker submit, replay, batch, or reorder transactions that reach `add_transactions` and cause effects, settlements, or checkpoints to apply twice or apply under a different digest than the one originally authorized?

## Target
- File/function: crates/sui-core/src/consensus_throughput_calculator.rs::add_transactions
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: timestamp_ms, num_of_transactions
- Exploit idea: Test duplicate submission, delayed finality, retry, and partial-write paths for mismatched replay protection.
- Invariant to test: A user transaction's effects must be committed at most once and always under the exact authenticated digest and object set.
- Expected Immunefi impact: Critical if balances or ownership can be duplicated; otherwise Medium for harmful state divergence.
- Fast validation: Locally resubmit the same signed transaction across retries and reordered dependencies and compare the number of committed effects.
