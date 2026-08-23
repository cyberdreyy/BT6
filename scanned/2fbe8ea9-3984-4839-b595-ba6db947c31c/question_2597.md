# Q2597: status_cache::do_test_shuttle_purge_nonce_overlap — status-cache dedup bypass

## Question
Can an unprivileged attacker, through a transaction committed to the Bank by an unprivileged fee-payer, reach `status_cache::do_test_shuttle_purge_nonce_overlap` and defeat status-cache dedup so an already-processed transaction is accepted again, so that the invariant "a transaction signature/blockhash pair is committed at most once" is violated, leading to Loss of Funds?

## Target
- File/function: `runtime/src/status_cache.rs` -> `do_test_shuttle_purge_nonce_overlap`
- Entrypoint: a transaction committed to the Bank by an unprivileged fee-payer
- Attacker controls: a transaction it resubmits within the blockhash validity window
- Exploit idea: Defeat status-cache dedup so an already-processed transaction is accepted again.
- Invariant to test: a transaction signature/blockhash pair is committed at most once.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a bank test committing the block on two independent banks and asserting identical bank hash / dedup behavior.
