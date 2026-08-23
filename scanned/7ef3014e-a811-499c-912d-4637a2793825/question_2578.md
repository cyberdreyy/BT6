# Q2578: status_cache::set_max_root_entries — blockhash-queue expiry edge

## Question
Can an unprivileged attacker, through a transaction committed to the Bank by an unprivileged fee-payer, reach `status_cache::set_max_root_entries` and exploit blockhash queue expiry so a transaction is valid on some validators and expired on others, so that the invariant "a blockhash is valid or expired identically on every validator" is violated, leading to Consensus/Safety Violation?

## Target
- File/function: `runtime/src/status_cache.rs` -> `set_max_root_entries`
- Entrypoint: a transaction committed to the Bank by an unprivileged fee-payer
- Attacker controls: the recent_blockhash it selects near the expiry boundary
- Exploit idea: Exploit blockhash queue expiry so a transaction is valid on some validators and expired on others.
- Invariant to test: a blockhash is valid or expired identically on every validator.
- Expected Immunefi impact: Consensus/Safety Violation — Critical
- Fast validation: write a bank test committing the block on two independent banks and asserting identical bank hash / dedup behavior.
